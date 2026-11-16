"""Genres and starter artists for signup.

Spotify no longer returns genres for this app, so genres come from this list
(and from Last.fm tags when a Last.fm key is configured).
"""
from concurrent.futures import ThreadPoolExecutor

import requests
from django.core.cache import cache

from . import artist_search, lastfm

GENRES = [
    ("afrobeats", "Afrobeats", ["Burna Boy", "Wizkid", "Davido", "Rema", "Tems", "Asake", "Ayra Starr", "Omah Lay", "Fireboy DML", "CKay"]),
    ("amapiano", "Amapiano", ["Kabza De Small", "DJ Maphorisa", "Tyler ICU", "Focalistic", "Uncle Waffles", "Young Stunna", "Kelvin Momo", "Major League DJz"]),
    ("hip-hop", "Hip-hop", ["Kendrick Lamar", "Drake", "J. Cole", "Travis Scott", "Olamide", "Kanye West", "Nicki Minaj", "21 Savage"]),
    ("rnb", "R&B", ["SZA", "Frank Ocean", "The Weeknd", "Summer Walker", "Brent Faiyaz", "Jhené Aiko", "Daniel Caesar", "H.E.R."]),
    ("pop", "Pop", ["Taylor Swift", "Dua Lipa", "Ed Sheeran", "Billie Eilish", "Ariana Grande", "Bruno Mars", "Olivia Rodrigo", "Sabrina Carpenter"]),
    ("gospel", "Gospel", ["Moses Bliss", "Sinach", "Nathaniel Bassey", "Mercy Chinwo", "Maverick City Music", "Kirk Franklin", "Dunsin Oyekan", "Tasha Cobbs Leonard"]),
    ("dancehall", "Dancehall & Reggae", ["Vybz Kartel", "Shenseea", "Popcaan", "Bob Marley & The Wailers", "Damian Marley", "Sean Paul", "Burna Boy", "Skillibeng"]),
    ("alte", "Alté", ["Cruel Santino", "Odunsi (The Engine)", "Lady Donli", "Show Dem Camp", "BOJ", "Tay Iwar", "Amaarae", "Prettyboy D-O"]),
    ("highlife", "Highlife & Afro-fusion", ["Flavour", "Phyno", "Fela Kuti", "King Sunny Adé", "Bez", "Adekunle Gold", "Simi", "Johnny Drille"]),
    ("electronic", "Electronic & House", ["Calvin Harris", "Black Coffee", "Fred again..", "Disclosure", "Kaytranada", "Daft Punk", "Peggy Gou", "Skrillex"]),
    ("rock", "Rock & Indie", ["Arctic Monkeys", "Coldplay", "Tame Impala", "Radiohead", "Paramore", "Linkin Park", "The 1975", "Imagine Dragons"]),
    ("soul-jazz", "Soul & Jazz", ["Lauryn Hill", "Erykah Badu", "Amy Winehouse", "Norah Jones", "Stevie Wonder", "Anita Baker", "Robert Glasper", "Lianne La Havas"]),
]

GENRE_KEYS = {key for key, _, _ in GENRES}
GENRE_LABELS = {key: label for key, label, _ in GENRES}
MAX_GENRES = 5
SUGGESTIONS_PER_GENRE = 8
CACHE_SECONDS = 60 * 60 * 24


def clean_genre_keys(keys):
    picked = []
    for key in keys:
        if key in GENRE_KEYS and key not in picked:
            picked.append(key)
    return picked[:MAX_GENRES]


def _seed_genres(name):
    folded = name.strip().casefold()
    return [key for key, _, names in GENRES if folded in {n.casefold() for n in names}]


def genres_for_artist(name):
    """Genres from the seed list first, then Last.fm tags if it's set up."""
    genres = _seed_genres(name)
    if lastfm.is_configured():
        genres += [tag for tag in lastfm.artist_tags(name) if tag not in genres]
    return genres


def _resolve(name):
    try:
        results = artist_search.search_artists(name, limit=3)
    except requests.exceptions.RequestException:
        return None
    for artist in results:
        if artist["name"].casefold() == name.casefold():
            return artist
    return results[0] if results else None


def suggestions_for_genre(key):
    cache_key = f"taste_suggestions:{key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    names = next((names for k, _, names in GENRES if k == key), [])[:SUGGESTIONS_PER_GENRE]
    with ThreadPoolExecutor(max_workers=8) as pool:
        artists = [a for a in pool.map(_resolve, names) if a]
    if artists:
        cache.set(cache_key, artists, CACHE_SECONDS)
    return artists


def suggestions_for_genres(keys, exclude_ids=()):
    seen, result = set(exclude_ids), []
    for key in keys:
        for artist in suggestions_for_genre(key):
            if artist["external_id"] not in seen:
                seen.add(artist["external_id"])
                result.append({**artist, "genre": key, "genre_label": GENRE_LABELS[key]})
    return result


# DISCOVER MORE ARTISTS (My Sound deck)
# A big, random pool per genre so people keep meeting new artists in their genres (and passing on the
# ones they don't like), instead of the same 8 starter names every time.

LASTFM_TAGS = {
    "afrobeats": ["afrobeats"], "amapiano": ["amapiano"], "hip-hop": ["hip-hop"], "rnb": ["rnb"],
    "pop": ["pop"], "gospel": ["gospel"], "dancehall": ["dancehall", "reggae"], "alte": ["alte"],
    "highlife": ["highlife"], "electronic": ["house", "electronic"], "rock": ["indie", "rock"],
    "soul-jazz": ["soul", "jazz"],
}
POOL_PAGES = 3          # Last.fm pages of 50 per tag: the genre's top ~150, so picks stay relevant
POOL_CACHE_SECONDS = 60 * 60 * 24
RESOLVED_CACHE_SECONDS = 60 * 60 * 24 * 7
_NOT_FOUND = {}


def _starter_names(key):
    return list(next((names for k, _, names in GENRES if k == key), []))


def artist_pool(key):
    """Artist names for a genre: our starter list + Last.fm's top artists for its tags.
    Never makes anyone wait: when the list is missing or a day old, it's rebuilt in the background and
    the last known list (or the starter list, the very first time) is used meanwhile."""
    fresh = cache.get(f"artist_pool:{key}")
    if fresh is not None:
        return fresh
    if lastfm.is_configured() and cache.add(f"artist_pool_building:{key}", 1, 60 * 5):
        _in_background(_build_pool, key)
    return cache.get(f"artist_pool_stale:{key}") or _starter_names(key)


def _in_background(fn, *args):
    import threading
    threading.Thread(target=fn, args=args, daemon=True).start()


def _build_pool(key):
    names, complete = _starter_names(key), True
    for tag in LASTFM_TAGS.get(key, []):
        for page in range(1, POOL_PAGES + 1):
            try:
                data = lastfm._call("tag.gettopartists", tag=tag, limit=50, page=page)
            except (lastfm.LastfmError, requests.exceptions.RequestException, ValueError):
                complete = False
                break
            names += [a["name"] for a in lastfm._as_list(data.get("topartists", {}).get("artist")) if a.get("name")]
    seen, pool = set(), []
    for name in names:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            pool.append(name)
    cache.set(f"artist_pool:{key}", pool, POOL_CACHE_SECONDS if complete else 60 * 10)  # retry soon after a hiccup
    cache.set(f"artist_pool_stale:{key}", pool, POOL_CACHE_SECONDS * 30)
    cache.delete(f"artist_pool_building:{key}")
    return pool


def resolve_artist(name):
    """Spotify artist (id, photo) for a name, remembered for a week. None if Spotify doesn't know them."""
    cache_key = f"resolved_artist:{name.casefold()}"
    hit = cache.get(cache_key)
    if hit is not None:
        return hit or None
    artist = _resolve(name)
    cache.set(cache_key, artist or _NOT_FOUND, RESOLVED_CACHE_SECONDS if artist is not None else 60 * 10)
    return artist


def random_deck_artist(genre_keys, skip_ids=(), skip_names=(), queue=(), tries=12, rng=None):
    """A random artist from one of these genres that the person hasn't added or passed on yet.
    `queue` holds names picked (and looked up in the background) while the previous card was showing,
    so the next card usually appears straight away."""
    import random
    rng = rng or random
    keys = [k for k in genre_keys if k in GENRE_KEYS] or [k for k, _, _ in GENRES]
    skip_ids, skip_names, tried = set(skip_ids), {n.casefold() for n in skip_names}, set()
    for name, key in queue:
        if key in GENRE_KEYS and name.casefold() not in skip_names:
            tried.add(name.casefold())
            artist = resolve_artist(name)
            if artist and artist["external_id"] not in skip_ids:
                return {**artist, "genre": key, "genre_label": GENRE_LABELS[key]}
    for _ in range(tries):
        key = rng.choice(keys)
        candidates = [n for n in artist_pool(key) if n.casefold() not in skip_names and n.casefold() not in tried]
        if not candidates:
            keys = [k for k in keys if k != key] or keys
            continue
        name = rng.choice(candidates)
        tried.add(name.casefold())
        artist = resolve_artist(name)
        if artist and artist["external_id"] not in skip_ids:
            return {**artist, "genre": key, "genre_label": GENRE_LABELS[key]}
    return None


def plan_next_cards(genre_keys, skip_names=(), count=3, rng=None):
    """Pick the next few deck cards now and look them up on Spotify in the background."""
    import random
    rng = rng or random
    keys = [k for k in genre_keys if k in GENRE_KEYS] or [k for k, _, _ in GENRES]
    skip = {n.casefold() for n in skip_names}
    picks = []
    for _ in range(count * 4):
        if len(picks) >= count:
            break
        key = rng.choice(keys)
        candidates = [n for n in artist_pool(key) if n.casefold() not in skip]
        if candidates:
            name = rng.choice(candidates)
            skip.add(name.casefold())
            picks.append((name, key))
    if picks:
        _in_background(_warm, [name for name, _ in picks])
    return picks


def _warm(names):
    with ThreadPoolExecutor(max_workers=3) as pool:  # all at once, not one after another
        list(pool.map(resolve_artist, names))

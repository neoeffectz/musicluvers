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

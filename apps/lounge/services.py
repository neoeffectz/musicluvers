"""Friday Lounge: a pool of song requests, played as soon as it's full, with music in between.

How a session runs (one playlist a week, Fridays from 5pm; see schedule.py):
  * People request songs, one each (they can change it until the playlist starts).
  * As soon as POOL_SIZE different songs are in, they play, in random order, for everyone. When they
    finish, the Lounge closes for the week. No full playlist within 15 minutes: it closes then.
  * While the songs come in, filler music plays: random songs from people's profiles, with no names
    on them (so the music isn't a popularity contest). Prepared at 4:30pm so it's ready at 5.
Nothing runs on a timer: the state moves on whenever someone loads or refreshes the Lounge (pages
refresh themselves at each song change), so it costs nothing while nobody is there.
"""
import logging
import random

import requests
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.spotify_match import artist_search

from . import schedule, youtube
from .models import Presence, Round, SongRequest

logger = logging.getLogger(__name__)
POOL_SIZE = settings.LOUNGE_POOL_SIZE      # songs per pool (5 while the Lounge is small)
FILLER_MINUTES = 10                         # each batch of filler music
FILLER_SONGS = 4
FILLER_NUMBERS = 1000                       # filler batches are numbered 1000, 1001... (pools are 1, 2...)
PREPARE_SONGS = 12                          # found on YouTube at 4:30pm, so later filler is free
DEFAULT_SONG_MS = 210_000          # when Spotify doesn't tell us the length
MAX_SONG_MS = 300_000              # long songs play for 5 minutes, so one 9-minute track can't hog a pool
PRESENCE_SECONDS = 90              # "in the Lounge" = the page checked in within this long
ACTIVE_DAYS = 30
GROUP = "lounge"
# The very first Friday has no profile songs or history to use.
STARTER_SONGS = [("Essence", "Wizkid"), ("Calm Down", "Rema"), ("Free Mind", "Tems"),
                 ("Last Last", "Burna Boy"), ("Rush", "Ayra Starr")]


def play_ms(request):
    return min(request.duration_ms or DEFAULT_SONG_MS, MAX_SONG_MS)


def _same_song(name, artist):
    return (" ".join((name or "").lower().split()), " ".join((artist or "").lower().split()))


# POOLS

def open_round(at=None):
    """Tonight's playlist pool (one a week), created when the first person comes in."""
    at = at or schedule.now()
    current, _ = Round.objects.get_or_create(session_date=schedule.session_date(at), number=1,
                                             is_warmup=False, defaults={"opened_at": at})
    return current


def pool_count(round_):
    """How many different songs are in the pool (two people asking for the same song count once)."""
    return len({_same_song(n, a) for n, a in round_.requests.values_list("track_name", "artist_name")})


def playlist(round_):
    """A pool's (or filler batch's) songs in play order, each once. Everyone who requested the same song
    shares its slot: song.requesters lists them all ("Requested by Chidi & Funmi"); filler has none."""
    songs = {}
    for request in round_.requests.filter(position__isnull=False).select_related("user").order_by("position", "created_at"):
        if request.position in songs:
            if request.user:
                songs[request.position].requesters.append(request.user)
        else:
            request.requesters = [request.user] if request.user else []
            songs[request.position] = request
    return list(songs.values())


def round_end(round_, songs=None):
    songs = playlist(round_) if songs is None else songs
    return round_.started_at + timedelta(milliseconds=sum(play_ms(s) for s in songs))


def playing_round(at=None):
    """What's playing now: the latest pool if it's still going, else the filler batch that followed it."""
    at = at or schedule.now()
    day = schedule.session_date(at)
    pool = (Round.objects.filter(session_date=day, is_warmup=False, started_at__isnull=False, started_at__lte=at)
            .order_by("-number").first())
    if pool:
        songs = playlist(pool)
        if songs and round_end(pool, songs) > at:
            return pool, songs
    filler = Round.objects.filter(session_date=day, is_warmup=True, started_at__lte=at).order_by("-started_at").first()
    # Filler that was cut off by a pool never comes back.
    if filler and (pool is None or filler.started_at >= pool.started_at):
        songs = playlist(filler)
        if songs and round_end(filler, songs) > at:
            return filler, songs
    return None, []


def advance(at=None):
    """Play the pool once it's full and no other pool is playing. Safe to call from every request."""
    at = at or schedule.now()
    if not schedule.is_open(at):
        return None
    current, _ = playing_round(at)
    if current is not None and not current.is_warmup:
        return None                       # a pool is playing: the next one waits its turn
    with transaction.atomic():
        round_ = Round.objects.select_for_update().filter(id=open_round(at).id, is_warmup=False).first()
        if round_ is None or round_.started_at is not None:
            return None
        by_song = {}   # same title and artist = one song, even under two Spotify listings
        for request in round_.requests.all():
            by_song.setdefault(_same_song(request.track_name, request.artist_name), []).append(request)
        if len(by_song) < POOL_SIZE:
            return None
        # Random order. (If two songs arrive at the very same moment there can be 6: a random 5 play.)
        # A song YouTube can't play still plays, through Spotify on the page.
        picks = random.sample(list(by_song.values()), POOL_SIZE)
        videos = youtube.find_videos([group[0] for group in picks])
        for position, group in enumerate(picks):
            video_id, video_ms = videos.get(group[0].id, ("", 0))
            fields = {"position": position, "youtube_id": video_id}
            if video_ms:
                fields["duration_ms"] = video_ms   # the video's real length keeps everyone in sync
            SongRequest.objects.filter(id__in=[r.id for r in group]).update(**fields)
        round_.started_at = at
        round_.save(update_fields=["started_at"])
    transaction.on_commit(lambda: broadcast({"kind": "stage"}))
    return round_


# FILLER MUSIC: whenever no pool is playing

def ensure_filler(at=None):
    """Start a batch of filler music if nothing is playing."""
    at = at or schedule.now()
    if not schedule.is_open(at) or playing_round(at)[0] is not None:
        return None
    day = schedule.session_date(at)
    number = FILLER_NUMBERS + Round.objects.filter(session_date=day, is_warmup=True).count()
    try:
        with transaction.atomic():
            return build_filler(day, at, number)
    except IntegrityError:             # someone else started it at the same moment
        return None


def build_filler(day, start, number=FILLER_NUMBERS):
    """About FILLER_MINUTES of anonymous music starting at `start`: random profile songs not played yet
    today, then last Friday's, then a starter list."""
    round_, created = Round.objects.get_or_create(session_date=day, number=number,
                                                  defaults={"opened_at": start, "started_at": start, "is_warmup": True})
    if not created:
        return round_
    avoid = set()
    for s in SongRequest.objects.filter(round__session_date=day, position__isnull=False):
        avoid.update({s.track_id, _same_song(s.track_name, s.artist_name)})
    songs = _filler_songs(day, avoid)
    total, position = 0, 0
    for song in songs:
        if total >= FILLER_MINUTES * 60_000 or position >= FILLER_SONGS:
            break
        req = SongRequest.objects.create(round=round_, position=position, user=None, **song)  # no names on filler
        total += play_ms(req)
        position += 1
    return round_


NEW_LOOKUPS = 4     # at most this many YouTube searches per filler batch (the rest must already be saved)


def _filler_songs(day, avoid):
    """Filler music only plays songs that are on YouTube (the shared player), never the Spotify fallback.
    Candidates: profile songs, last Friday's playlist, then anything that has played in the Lounge before.
    Saved YouTube matches are free; a few new ones are looked up while YouTube's daily limit allows."""
    candidates, seen = [], set(avoid)
    for song in _profile_songs(day, avoid, limit=40) + _last_friday_songs(day, avoid, limit=40) + _played_before(avoid):
        same = _same_song(song["track_name"], song["artist_name"])
        if song["track_id"] in seen or same in seen:
            continue
        seen.update({song["track_id"], same})
        candidates.append(song)
    saved = [s for s in candidates if s.get("youtube_id") or youtube.known_video(s["track_id"])]
    new = [s for s in candidates if s not in saved][:NEW_LOOKUPS]
    found = youtube.find_videos([SongRequest(id=i, **s) for i, s in enumerate(saved + new)])
    playable = []
    for i, song in enumerate(saved + new):
        video_id, video_ms = found.get(i, ("", 0))
        video_id = video_id or song.get("youtube_id", "")
        if not video_id:
            continue                     # not on YouTube (or no lookups left today): skip it
        playable.append({**song, "youtube_id": video_id, "duration_ms": video_ms or song.get("duration_ms", 0)})
    random.shuffle(playable)
    if playable:
        return playable
    # Nothing on YouTube at all yet (a brand-new Lounge with no lookups left): better a Spotify song than silence.
    return candidates[:FILLER_SONGS] or _starter_songs()


def _played_before(avoid, limit=40):
    """Songs that have played in the Lounge on earlier days, already found on YouTube (free)."""
    rows = list(SongRequest.objects.filter(position__isnull=False).exclude(youtube_id="")
                .values("track_id", "track_name", "artist_name", "image_url", "duration_ms", "youtube_id")
                .order_by("-created_at")[:limit * 3])
    random.shuffle(rows)
    return [r for r in rows if r["track_id"] not in avoid][:limit]


def prepare_warmup(day=None):
    """Friday 4:30pm (scheduled): find a dozen profile songs on YouTube on a fresh daily limit (saved, so
    every later filler batch is free) and line up the 5pm music."""
    from datetime import datetime
    day = day or schedule.now().date()
    candidates = _profile_songs(day, set(), limit=PREPARE_SONGS)
    youtube.find_videos([SongRequest(id=i, **song) for i, song in enumerate(candidates)])
    return build_filler(day, datetime.combine(day, schedule.OPENS))


def _profile_songs(day, avoid, limit=FILLER_SONGS):
    """Random song answers from people active in the last month, one per person; songs already found on
    YouTube first (free). Returned without the person: filler music carries no names."""
    from datetime import datetime, timedelta as td
    from apps.spotify_match.models import ProfilePrompt
    labels = dict(ProfilePrompt.PROMPT_CHOICES)
    song_questions = [k for k in labels if k not in ProfilePrompt.PROMPT_KINDS]
    since = datetime.combine(day, schedule.OPENS) - td(days=ACTIVE_DAYS)
    answers = list(ProfilePrompt.objects.filter(prompt__in=song_questions, user__is_active=True)
                   .filter(Q(user__last_login__gte=since) | Q(user__date_joined__gte=since)))
    random.shuffle(answers)
    free = {a.track_id for a in answers if youtube.known_video(a.track_id)}
    answers.sort(key=lambda a: a.track_id not in free)       # free ones first, still random within each group
    picked, people, seen = [], set(), set(avoid)
    for a in answers:
        same = _same_song(a.track_name, a.artist_name)
        if a.user_id in people or a.track_id in seen or same in seen:
            continue
        people.add(a.user_id)
        seen.update({a.track_id, same})
        picked.append(dict(track_id=a.track_id, track_name=a.track_name, artist_name=a.artist_name,
                           image_url=a.image_url, duration_ms=0))
        if len(picked) >= limit:
            break
    return picked


def _last_friday_songs(day, avoid, limit=FILLER_SONGS):
    last_day = (SongRequest.objects.filter(round__session_date__lt=day, round__is_warmup=False,
                                           position__isnull=False).exclude(youtube_id="")
                .order_by("-round__session_date").values_list("round__session_date", flat=True).first())
    if not last_day:
        return []
    pool = list(SongRequest.objects.filter(round__session_date=last_day, round__is_warmup=False,
                                           position__isnull=False).exclude(youtube_id=""))
    random.shuffle(pool)
    songs, seen = [], set(avoid)
    for s in pool:
        same = _same_song(s.track_name, s.artist_name)
        if s.track_id not in seen and same not in seen:
            seen.update({s.track_id, same})
            songs.append(dict(track_id=s.track_id, track_name=s.track_name, artist_name=s.artist_name,
                              image_url=s.image_url, duration_ms=s.duration_ms, youtube_id=s.youtube_id))
    return songs[:limit]


def _starter_songs():
    """The very first Friday: a few well-known songs (Spotify for the cover and length)."""
    found = []
    for name, artist in STARTER_SONGS:
        try:
            hit = artist_search.search_tracks(f"{name} {artist}", limit=1)
            if not hit:
                continue
            item = artist_search._get(artist_search.TRACK_URL.format(id=hit[0]["track_id"]))
            found.append(dict(**artist_search._to_track(item), duration_ms=item.get("duration_ms") or 0))
        except Exception:
            continue
    return found


# WHAT THE PAGE SHOWS

def played_today(day, at, limit=15):
    """Pool songs that have finished this evening, newest first: you can still tap who picked them."""
    done = []
    for pool in Round.objects.filter(session_date=day, is_warmup=False, started_at__isnull=False, started_at__lte=at):
        cursor = pool.started_at
        for song in playlist(pool):
            cursor += timedelta(milliseconds=play_ms(song))
            if cursor <= at:
                done.append((cursor, song))
    done.sort(key=lambda pair: pair[0], reverse=True)
    return [song for _, song in done[:limit]]


def stage(user, at=None):
    """Everything the Lounge page needs right now."""
    from apps.users import safety
    at = at or schedule.now()
    advance(at)
    ensure_filler(at)
    round_ = open_round(at)
    playing, songs = playing_round(at)
    now_playing, up_next, seconds_left, elapsed = None, [], None, 0
    if playing:
        cursor = playing.started_at
        for song in songs:
            end = cursor + timedelta(milliseconds=play_ms(song))
            if end > at and now_playing is None:
                now_playing, seconds_left = song, max(1, int((end - at).total_seconds()))
                elapsed = max(0.0, (at - cursor).total_seconds())
            elif end > at:
                up_next.append(song)
            cursor = end
    filler = bool(playing and playing.is_warmup)
    mine = round_.requests.filter(user=user).first()
    playing_mine = None if filler else next((s for s in songs if any(u.id == user.id for u in s.requesters)), None)
    return {
        "round": round_,
        "my_request": mine,
        "my_pick": playing_mine,
        "pool_count": pool_count(round_),
        "pool_size": POOL_SIZE,
        "requests_closed": bool(round_.started_at),        # tonight's playlist is on: no more requests
        "now_playing": now_playing,
        "seconds_left": seconds_left,
        "elapsed": round(elapsed, 1),   # seconds into the current song: late joiners start right here
        "up_next": [] if filler else up_next,
        "played": played_today(round_.session_date, at),
        "refresh_in": (seconds_left or 20) + 1,
        "warmup": filler,
        "blocked": safety.blocked_ids(user),
        "closes_at": schedule.closes_at(at),
    }


# REQUESTS

def request_song(user, track_id, at=None):
    """Request (or change) this person's song for the open round. Returns the SongRequest."""
    at = at or schedule.now()
    if not schedule.is_open(at):
        raise ValueError("The Lounge is closed. It opens Fridays at 5pm.")
    try:
        item = artist_search._get(artist_search.TRACK_URL.format(id=track_id))  # full track: includes its length
        track, length = artist_search._to_track(item), item.get("duration_ms") or 0
    except requests.exceptions.RequestException:
        # Spotify busy: a song the Lounge already knows can still be requested
        from apps.spotify_match.models import ProfilePrompt
        known = (SongRequest.objects.filter(track_id=track_id).order_by("-created_at").first()
                 or ProfilePrompt.objects.filter(track_id=track_id).first())
        if known is None:
            raise
        track = {"track_id": known.track_id, "track_name": known.track_name, "artist_name": known.artist_name,
                 "image_url": known.image_url}
        length = getattr(known, "duration_ms", 0)   # unknown: YouTube's video length is used when it plays
    round_ = open_round(at)
    if round_.started_at:
        raise ValueError("Tonight's 5 songs are already playing. Requests open again next Friday.")
    # No song twice in one evening: once it has played (or is playing), it can't be asked for again.
    same = _same_song(track.get("track_name"), track.get("artist_name"))
    started = (SongRequest.objects.filter(round__session_date=round_.session_date, round__is_warmup=False,
                                          round__started_at__isnull=False, position__isnull=False)
               .values_list("track_id", "track_name", "artist_name"))
    if any(t == track_id or _same_song(n, a) == same for t, n, a in started):
        raise ValueError("That song has already played tonight. Pick another one.")
    request, _ = SongRequest.objects.update_or_create(
        round=round_, user=user,
        defaults={**track, "duration_ms": length, "position": None},
    )
    transaction.on_commit(lambda: broadcast({"kind": "requests", "n": pool_count(round_)}))
    advance(at)
    return request


def search_known_songs(query, limit=6):
    """Songs the Lounge has played or been asked for, by title or artist: search still works (for these)
    while Spotify is busy."""
    query = " ".join(query.split())
    if len(query) < 2:
        return []
    from apps.spotify_match.models import ProfilePrompt
    found, seen = [], set()
    match = Q(track_name__icontains=query) | Q(artist_name__icontains=query)
    fields = ("track_id", "track_name", "artist_name", "image_url")
    requested = SongRequest.objects.filter(match).order_by("-created_at").values(*fields)[:60]
    on_profiles = (ProfilePrompt.objects.filter(match).exclude(prompt__in=ProfilePrompt.PROMPT_KINDS)
                   .order_by("-created_at").values(*fields)[:60])     # song answers only, not artists/albums
    for row in [*requested, *on_profiles]:
        same = _same_song(row["track_name"], row["artist_name"])     # one row per song, whatever its listing
        if same not in seen:
            seen.add(same)
            found.append(row)
    return found[:limit]


# WHO'S HERE

def check_in(user, at=None):
    Presence.objects.update_or_create(user=user, defaults={"last_seen": at or schedule.now()})


def check_out(user):
    Presence.objects.filter(user=user).delete()


def people_here(at=None):
    at = at or schedule.now()
    return Presence.objects.filter(last_seen__gte=at - timedelta(seconds=PRESENCE_SECONDS)).count()


def broadcast(event):
    """Tell every open Lounge page (best effort: pages also refresh on their own timer)."""
    import logging
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    try:
        async_to_sync(get_channel_layer().group_send)(GROUP, {"type": "lounge_event", "event": event})
    except Exception:
        logging.getLogger(__name__).exception("Lounge broadcast failed")


# A REQUESTER'S CARD

def person_card(viewer, other):
    """What you see when you tap someone who requested a song (None if you can't see them)."""
    from apps.spotify_match import services as match
    from apps.spotify_match.models import MatchScore, Swipe
    from apps.users import safety
    if other.id == viewer.id or not other.is_active or safety.is_blocked_pair(viewer, other):
        return None
    a, b = sorted([viewer, other], key=lambda u: str(u.id))
    score = MatchScore.objects.filter(user_a=a, user_b=b).first()
    from apps.spotify_match import distance
    km = distance.km_between(viewer, other)
    return {
        "user": other,
        "score": score.display_score if score else None,
        "shared_artists": score.shared_artists if score else [],
        "top_artists": list(other.top_artists.order_by("rank")[:8]),
        "prompts": match.answered_prompts(other),
        "distance": distance.label(km),
        "can_like": match._visible_to_each_other(viewer, other),
        "liked": Swipe.objects.filter(from_user=viewer, to_user=other, liked=True).exists(),
        "matched": match.Match.objects.filter(Q(user_a=a, user_b=b)).exists(),
    }

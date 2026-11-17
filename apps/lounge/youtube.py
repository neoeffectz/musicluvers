"""Find the YouTube video for a requested song, so the Lounge can play full songs, in sync, for everyone.

Only drawn and warm-up songs are looked up, and every match is saved for good (TrackVideo), so a song
costs YouTube quota once, ever (a search is 100 of the free 10,000 daily units, a length lookup 1).
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from datetime import timedelta

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
# Not the song as released: a performance, a cover, an edit.
_WRONG_VERSION = re.compile(r"\b(live|performance|performs|concert|tour|tiny desk|session|acoustic|cover|remix|"
                            r"sped ?up|slowed|reverb|8d|karaoke|instrumental|nightcore|mashup|reaction|dance video)\b")


def is_configured():
    return bool(settings.YOUTUBE_API_KEY)


def _seconds(iso):
    m = _DURATION.fullmatch(iso or "")
    if not m:
        return 0
    h, mins, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mins * 60 + s


def find_video(track_id, track_name, artist_name, duration_ms=0):
    """(video_id, duration_ms) for a song, or ("", 0) when YouTube has no playable match.
    Looks at the top 5 results for "<song> <artist> official audio" and prefers the official audio upload
    ("Official Audio"/"Visualizer" on the artist's channel): its picture is just the album cover, so the
    Lounge feels like music, not a TV. Among those, the one whose length matches the real song wins
    (so a 19-minute loop or a long music-video skit loses to the track itself)."""
    from django.utils import timezone
    from .models import TrackVideo
    known = TrackVideo.objects.filter(track_id=track_id).first()
    # A found video is kept for good; "nothing playable" is retried after 30 days (uploads change).
    if known and (known.youtube_id or known.looked_up_at > timezone.now() - timedelta(days=30)):
        return known.youtube_id, known.duration_ms
    if not is_configured():
        return "", 0
    try:
        found = requests.get(SEARCH_URL, timeout=8, params={
            # no category filter: some artists file their official audio outside YouTube's "Music" category
            "part": "id", "type": "video", "videoEmbeddable": "true",
            "maxResults": 5, "q": f"{track_name} {artist_name} official audio".strip()[:120], "key": settings.YOUTUBE_API_KEY,
        })
        found.raise_for_status()
        ids = [item["id"]["videoId"] for item in found.json().get("items", [])]
        best = None
        if ids:
            info = requests.get(VIDEOS_URL, timeout=8, params={
                "part": "snippet,contentDetails,status", "id": ",".join(ids), "key": settings.YOUTUBE_API_KEY})
            info.raise_for_status()
            target = (duration_ms or 0) / 1000
            for rank, video in enumerate(info.json().get("items", [])):
                if not video.get("status", {}).get("embeddable", True):
                    continue
                seconds = _seconds(video.get("contentDetails", {}).get("duration"))
                if not seconds or seconds > 15 * 60:
                    continue
                off_by = abs(seconds - target) if target else rank * 30
                snippet = video.get("snippet", {})
                title = snippet.get("title", "").lower()
                # Cover art, not a music video. (Not YouTube's auto-generated "- Topic" tracks: many of those
                # silently refuse to play on other sites.)
                topic = snippet.get("channelTitle", "").endswith(" - Topic")
                audio = not topic and ("audio" in title or "visuali" in title)
                wrong = bool(_WRONG_VERSION.search(title)) and not _WRONG_VERSION.search(track_name.lower())
                key = (wrong, off_by > 20, topic, not audio, off_by, rank)
                if best is None or key < best[0]:
                    best = (key, video["id"], seconds * 1000)
        result = (best[1], best[2]) if best else ("", 0)
        TrackVideo.objects.update_or_create(track_id=track_id,
                                            defaults={"youtube_id": result[0], "duration_ms": result[1]})
        return result
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        # Not cached: try again next time. The error text holds the request address, key included,
        # so the key is blanked out before it reaches the logs.
        logger.warning("YouTube lookup failed for %s: %s", track_id,
                       str(e).replace(settings.YOUTUBE_API_KEY, "<key>") if settings.YOUTUBE_API_KEY else e)
        return "", 0


def known_video(track_id):
    """The saved YouTube match for a song, without spending any of YouTube's daily limit."""
    from .models import TrackVideo
    known = TrackVideo.objects.filter(track_id=track_id).exclude(youtube_id="").first()
    return (known.youtube_id, known.duration_ms) if known else None


def find_videos(songs):
    """Look up several songs at once. Saved matches are read straight away (free); only new songs go to
    YouTube, in parallel so a draw stays quick."""
    if not songs:
        return {}
    from django.utils import timezone
    from django.db import connection
    from .models import TrackVideo
    saved = {t.track_id: t for t in TrackVideo.objects.filter(track_id__in=[s.track_id for s in songs])}
    results, todo = {}, []
    for s in songs:
        known = saved.get(s.track_id)
        if known and (known.youtube_id or known.looked_up_at > timezone.now() - timedelta(days=30)):
            results[s.id] = (known.youtube_id, known.duration_ms)
        else:
            todo.append(s)
    if not todo or not is_configured():
        return {**{s.id: ("", 0) for s in todo}, **results}

    def look_up(s):
        try:
            return find_video(s.track_id, s.track_name, s.artist_name, s.duration_ms)
        finally:
            connection.close()   # each worker thread opened its own database connection

    with ThreadPoolExecutor(max_workers=min(10, len(todo))) as pool:
        found = list(pool.map(look_up, todo))
    return {**results, **{song.id: result for song, result in zip(todo, found)}}

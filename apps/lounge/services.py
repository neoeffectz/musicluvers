"""Friday Lounge: rounds of song requests, a random draw of 10, and a shared "Now playing".

How a session runs (Fridays 5pm-7pm):
  * Round 1 opens for requests at 5pm. One request per person per round (they can change it).
  * After REQUEST_MINUTES, the next time anyone looks at the Lounge, up to PICKS requests are drawn at
    random and become the playlist; it starts playing straight away. Round 2 opens for requests at once.
  * When a playlist ends and the open round has had its REQUEST_MINUTES (and has requests), it's drawn.
Nothing runs on a timer: the state moves on whenever someone loads or refreshes the Lounge (pages
refresh themselves at each song change), so it costs nothing while nobody is there.
"""
import random
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from apps.spotify_match import artist_search

from . import schedule
from .models import Presence, Round, SongRequest

REQUEST_MINUTES = 10
PICKS = 10
DEFAULT_SONG_MS = 210_000          # when Spotify doesn't tell us the length
MAX_SONG_MS = 300_000              # long songs play for 5 minutes, so one 9-minute track can't hog a round
PRESENCE_SECONDS = 90              # "in the Lounge" = the page checked in within this long
GROUP = "lounge"


def play_ms(request):
    return min(request.duration_ms or DEFAULT_SONG_MS, MAX_SONG_MS)


# ROUNDS

def open_round(at=None):
    """The round currently taking requests (creates round 1 when the session starts)."""
    at = at or schedule.now()
    day = schedule.session_date(at)
    current = Round.objects.filter(session_date=day, started_at__isnull=True).order_by("-number").first()
    if current:
        return current
    last = Round.objects.filter(session_date=day).order_by("-number").first()
    # Round 1 takes requests from 5pm (not from whenever the first person turned up).
    current, _ = Round.objects.get_or_create(session_date=day, number=(last.number + 1) if last else 1,
                                             defaults={"opened_at": at if last else schedule.session_start(at)})
    return current


def playlist(round_):
    return list(round_.requests.filter(position__isnull=False).select_related("user").order_by("position"))


def round_end(round_, songs=None):
    songs = playlist(round_) if songs is None else songs
    return round_.started_at + timedelta(milliseconds=sum(play_ms(s) for s in songs))


def playing_round(at=None):
    """The round whose playlist is playing now, with its songs, or (None, [])."""
    at = at or schedule.now()
    latest = (Round.objects.filter(session_date=schedule.session_date(at), started_at__isnull=False)
              .order_by("-number").first())
    if latest is None:
        return None, []
    songs = playlist(latest)
    if not songs or round_end(latest, songs) <= at:
        return None, []
    return latest, songs


def advance(at=None):
    """Draw the open round when its time has come. Safe to call from every request: only one wins."""
    at = at or schedule.now()
    if not schedule.is_open(at):
        return None
    current, _ = playing_round(at)
    if current is not None:
        return None
    with transaction.atomic():
        round_ = Round.objects.select_for_update().filter(id=open_round(at).id).first()
        if round_ is None or round_.started_at is not None:
            return None
        if at - round_.opened_at < timedelta(minutes=REQUEST_MINUTES):
            return None
        requests = list(round_.requests.all())
        if not requests:
            return None
        for position, request in enumerate(random.sample(requests, min(PICKS, len(requests)))):
            SongRequest.objects.filter(id=request.id).update(position=position)
        round_.started_at = at
        round_.save(update_fields=["started_at"])
        Round.objects.get_or_create(session_date=round_.session_date, number=round_.number + 1,
                                    defaults={"opened_at": at})
    transaction.on_commit(lambda: broadcast({"kind": "stage"}))
    return round_


# WHAT THE PAGE SHOWS

def stage(user, at=None):
    """Everything the Lounge page needs right now."""
    at = at or schedule.now()
    advance(at)
    round_ = open_round(at)
    playing, songs = playing_round(at)
    now_playing, up_next, played, seconds_left = None, [], [], None
    if playing:
        cursor = playing.started_at
        for song in songs:
            end = cursor + timedelta(milliseconds=play_ms(song))
            if end <= at:
                played.append(song)
            elif now_playing is None:
                now_playing, seconds_left = song, max(1, int((end - at).total_seconds()))
            else:
                up_next.append(song)
            cursor = end
    draw_in = max(0, int((round_.opened_at + timedelta(minutes=REQUEST_MINUTES) - at).total_seconds()))
    # The page refreshes itself at the next change: song ends, or the draw is due.
    refresh_in = seconds_left if seconds_left else (draw_in or 20)
    return {
        "round": round_,
        "my_request": round_.requests.filter(user=user).first(),
        "my_pick": next((s for s in songs if s.user_id == user.id), None),  # their song in the playing list
        "request_count": round_.requests.count(),
        "draw_in": draw_in,
        "draw_in_text": f"{draw_in // 60}:{draw_in % 60:02d}",
        "now_playing": now_playing,
        "seconds_left": seconds_left,
        "up_next": up_next,
        "played": played,
        "refresh_in": refresh_in + 1,
        "closes_at": schedule.closes_at(at),
    }


# REQUESTS

def request_song(user, track_id, at=None):
    """Request (or change) this person's song for the open round. Returns the SongRequest."""
    at = at or schedule.now()
    if not schedule.is_open(at):
        raise ValueError("The Lounge is closed. It opens Fridays at 5pm.")
    item = artist_search._get(artist_search.TRACK_URL.format(id=track_id))  # full track: includes its length
    track = artist_search._to_track(item)
    round_ = open_round(at)
    request, _ = SongRequest.objects.update_or_create(
        round=round_, user=user,
        defaults={**track, "duration_ms": item.get("duration_ms") or 0, "position": None},
    )
    transaction.on_commit(lambda: broadcast({"kind": "requests", "n": round_.requests.count()}))
    advance(at)
    return request


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

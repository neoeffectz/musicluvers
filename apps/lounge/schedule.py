"""When the Lounge is open, Lagos time (settings.TIME_ZONE).

One playlist a week, while the app is young: the Lounge opens Fridays at 5pm, waits up to 15 minutes for
5 songs, plays them, and closes when they finish. If 5 songs aren't in by 5:15pm, it closes then.

LOUNGE_ALWAYS_OPEN=True (env) opens it any day, for trying it out with friends: the session then starts
when the first person comes in, and runs the same way (once per day).
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

WEEKDAY = 4                          # Friday (Monday = 0)
OPENS = time(17, 0)
WAIT_FOR_SONGS = timedelta(minutes=15)   # no full playlist by then: the Lounge closes for the week


def now():
    return timezone.now()  # USE_TZ is off: this is Lagos local time


def session_date(at=None):
    return (at or now()).date()


def _playlist_round(day):
    from .models import Round
    return Round.objects.filter(session_date=day, is_warmup=False, number=1).first()


def session_start(at=None):
    """When today's Lounge opened, or None if it doesn't open today (or hasn't yet)."""
    at = at or now()
    if at.weekday() == WEEKDAY and at.time() >= OPENS:
        return datetime.combine(at.date(), OPENS)
    if settings.LOUNGE_ALWAYS_OPEN:
        pool = _playlist_round(at.date())
        return pool.opened_at if pool else at
    return None


def closes_at(at=None):
    """When the 5 songs finish, or 15 minutes after opening if they never all came in."""
    at = at or now()
    start = session_start(at)
    if start is None:
        return None
    pool = _playlist_round(at.date())
    if pool and pool.started_at:
        from .services import round_end
        return round_end(pool)
    return start + WAIT_FOR_SONGS


def is_open(at=None):
    at = at or now()
    end = closes_at(at)
    return end is not None and at < end


def next_opening(at=None):
    at = at or now()
    days = (WEEKDAY - at.weekday()) % 7
    opening = datetime.combine(at.date() + timedelta(days=days), OPENS)
    if opening <= at:
        opening += timedelta(days=7)
    return opening

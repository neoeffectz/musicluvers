"""When the Lounge is open: Fridays 5pm-6pm, Lagos time (settings.TIME_ZONE).

LOUNGE_ALWAYS_OPEN=True (env) opens it any day, for trying it out with friends.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

WEEKDAY = 4            # Friday (Monday = 0)
OPENS = time(17, 0)
CLOSES = time(18, 0)   # one hour: a denser crowd while the app is young


def now():
    return timezone.now()  # USE_TZ is off: this is Lagos local time


def is_open(at=None):
    at = at or now()
    if settings.LOUNGE_ALWAYS_OPEN:
        return True
    return at.weekday() == WEEKDAY and OPENS <= at.time() < CLOSES


def session_date(at=None):
    return (at or now()).date()


def closes_at(at=None):
    at = at or now()
    if settings.LOUNGE_ALWAYS_OPEN and not (at.weekday() == WEEKDAY and at.time() < CLOSES):
        return datetime.combine(at.date(), time(23, 59, 59))
    return datetime.combine(at.date(), CLOSES)


def session_start(at=None):
    """When today's Lounge opened: 5pm, or right now when the test switch opened it on another day."""
    at = at or now()
    start = datetime.combine(at.date(), OPENS)
    return start if start <= at else at


def next_opening(at=None):
    at = at or now()
    days = (WEEKDAY - at.weekday()) % 7
    opening = datetime.combine(at.date() + timedelta(days=days), OPENS)
    if opening <= at:
        opening += timedelta(days=7)
    return opening

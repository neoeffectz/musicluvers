"""Report and block. Blocking hides two people from each other everywhere and ends any match/chat."""
import logging

from django.conf import settings
from django.db import models, transaction

from .models import Block, CustomUser, Report

logger = logging.getLogger(__name__)


def blocked_ids(user):
    """Ids of everyone this user blocked or was blocked by (both directions are hidden).
    Remembered on the user object for the rest of the request (pages ask more than once)."""
    if "_blocked_ids" not in user.__dict__:
        pairs = Block.objects.filter(models.Q(blocker=user) | models.Q(blocked=user)).values_list("blocker_id", "blocked_id")
        user.__dict__["_blocked_ids"] = {b if a == user.id else a for a, b in pairs}
    return set(user.__dict__["_blocked_ids"])


def is_blocked_pair(a, b):
    return Block.objects.filter(models.Q(blocker=a, blocked=b) | models.Q(blocker=b, blocked=a)).exists()


def block(user, other):
    if other.id == user.id:
        raise ValueError("You can't block yourself.")
    from apps.spotify_match import services as match_services
    with transaction.atomic():
        Block.objects.get_or_create(blocker=user, blocked=other)
        user.__dict__.pop("_blocked_ids", None)
        other.__dict__.pop("_blocked_ids", None)
        match_services.unmatch(user, other)  # removes the match; the chat is deleted by the MATCH_REMOVED handler


def report(user, other, reason, details=""):
    """Reporting also blocks, like Tinder: you never see that person again while we review it."""
    if reason not in dict(Report.REASON_CHOICES):
        raise ValueError("Pick a reason.")
    if other.id == user.id:
        raise ValueError("You can't report yourself.")
    with transaction.atomic():
        item = Report.objects.create(reporter=user, reported=other, reason=reason, details=details.strip()[:1000])
        block(user, other)
    _tell_admin(item)
    return item


def _tell_admin(item):
    """Email the moderator (printed to the server log until real email is switched on)."""
    to = getattr(settings, "ADMIN_NOTIFICATION_EMAIL", "")
    if not to:
        logger.warning("New report %s (%s) — set ADMIN_NOTIFICATION_EMAIL to get these by email", item.id, item.reason)
        return
    from .emails import send_email
    send_email(
        to=to,
        subject=f"[Music Luvers] New report: {item.get_reason_display()}",
        text=(f"{item.reporter.first_name if item.reporter else 'Someone'} reported "
              f"{item.reported.first_name} ({item.reported.email}).\n"
              f"Reason: {item.get_reason_display()}\nDetails: {item.details or '-'}\n\n"
              f"Review it in the admin: /admin/users/report/{item.id}/change/"),
    )

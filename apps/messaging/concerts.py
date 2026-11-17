"""Our Concerts: concerts two matches plan together, inside their chat.

Either person proposes one (artist or event, venue, date and time, optional ticket link). It appears as a
card in the chat; the other person agrees or says "not this one" (which just removes it, no message).
Each person marks their own ticket. After the date it moves to "Done together", where either can love it.
"""
from datetime import timedelta
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from . import services
from .models import ConcertPlan

REMIND_EVERY = timedelta(hours=24)


def is_enabled():
    from django.conf import settings
    return settings.CONCERTS_ENABLED


def ticket_site_allowed(url):
    """Only real ticket sellers: a pasted link to a fake "ticket" page is an easy scam."""
    from django.conf import settings
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    host = parts.hostname.lower()
    return any(host == site or host.endswith("." + site) for site in settings.CONCERT_TICKET_SITES)


def _when(plan):
    return plan.starts_at.strftime("%a %-d %b, %-I:%M%p").replace(":00", "").replace("AM", "am").replace("PM", "pm")


def _check(conversation, user):
    """Only the two people in a chat, while they're still matched (and not blocked)."""
    if user.id not in (conversation.user_a_id, conversation.user_b_id):
        raise PermissionError("Not part of this conversation.")
    other = services.get_other_user(conversation, user)
    if not services._is_matched(user, other):
        raise PermissionError("You can only plan with people you're matched with.")
    return other


def propose(conversation, user, title, venue, starts_at, ticket_url=""):
    _check(conversation, user)
    title, venue, ticket_url = " ".join((title or "").split())[:120], " ".join((venue or "").split())[:120], (ticket_url or "").strip()
    if not title:
        raise ValueError("Add the artist or event.")
    if not venue:
        raise ValueError("Add the venue.")
    if starts_at is None:
        raise ValueError("Add the date and time.")
    if starts_at <= timezone.now():
        raise ValueError("Pick a date that hasn't passed yet.")
    if starts_at > timezone.now() + timedelta(days=730):
        raise ValueError("That date is more than two years away.")
    if ticket_url and not ticket_site_allowed(ticket_url):
        raise ValueError("Ticket links can only be from Tix.africa, Nairabox, Eventbrite, Ticketmaster or DICE.")
    with transaction.atomic():
        plan = ConcertPlan.objects.create(conversation=conversation, proposed_by=user, title=title, venue=venue,
                                          starts_at=starts_at, ticket_url=ticket_url[:500])
        message = services.send_message(conversation, user, f"Wants to go to {title} at {venue}, {_when(plan)}")
        message.concert = plan
        message.save(update_fields=["concert"])
    return plan


def respond(plan, user, agree):
    """The other person's answer. Agreeing posts a short note in the chat; 'not this one' just removes it."""
    _check(plan.conversation, user)
    if plan.proposed_by_id == user.id:
        raise PermissionError("Waiting for the other person to answer.")
    if plan.status != ConcertPlan.PROPOSED:
        return plan
    with transaction.atomic():
        plan.status = ConcertPlan.AGREED if agree else ConcertPlan.DECLINED
        plan.save(update_fields=["status"])
        if agree:
            note = services.send_message(plan.conversation, user, f"Agreed: {plan.title} at {plan.venue}, {_when(plan)}")
            note.concert = plan            # so open "Our Concerts" lists refresh live (shown as plain text)
            note.save(update_fields=["concert"])
    return plan


def remove(plan, user):
    """The person who proposed it can take it back while it's still waiting."""
    _check(plan.conversation, user)
    if plan.proposed_by_id != user.id or plan.status != ConcertPlan.PROPOSED:
        raise PermissionError("Only a plan you suggested, before it's answered, can be removed.")
    plan.status = ConcertPlan.DECLINED
    plan.save(update_fields=["status"])


def remind(plan, user):
    """Nudge the other person about a plan they haven't answered (at most once a day)."""
    _check(plan.conversation, user)
    if plan.proposed_by_id != user.id or plan.status != ConcertPlan.PROPOSED:
        raise PermissionError("Only a plan you suggested that's still waiting can be reminded.")
    if plan.reminded_at and timezone.now() - plan.reminded_at < REMIND_EVERY:
        raise ValueError("Reminder already sent today.")
    with transaction.atomic():
        plan.reminded_at = timezone.now()
        plan.save(update_fields=["reminded_at"])
        services.send_message(plan.conversation, user, f"Still up for {plan.title} at {plan.venue}, {_when(plan)}?")


def toggle_ticket(plan, user):
    _check(plan.conversation, user)
    if plan.status != ConcertPlan.AGREED:
        raise PermissionError("Tickets come after you've both agreed.")
    if plan.tickets.filter(id=user.id).exists():
        plan.tickets.remove(user)
    else:
        plan.tickets.add(user)


def toggle_love(plan, user):
    _check(plan.conversation, user)
    if plan.status != ConcertPlan.AGREED or plan.starts_at > timezone.now():
        raise PermissionError("You can love a concert after you've been.")
    if plan.loved_by.filter(id=user.id).exists():
        plan.loved_by.remove(user)
    else:
        plan.loved_by.add(user)


def board(conversation, user):
    """What 'Our Concerts' shows: upcoming (agreed and waiting) and done together."""
    _check(conversation, user)
    now = timezone.now()
    plans = list(conversation.concerts.exclude(status=ConcertPlan.DECLINED)
                 .select_related("proposed_by").prefetch_related("tickets", "loved_by"))
    for p in plans:
        p.days_to_go = max(0, (p.starts_at.date() - now.date()).days)
        p.when = _when(p)
        p.has_ticket_ids = {u.id for u in p.tickets.all()}
        p.loved_ids = {u.id for u in p.loved_by.all()}
        p.can_remind = p.status == ConcertPlan.PROPOSED and p.proposed_by_id == user.id and (
            not p.reminded_at or now - p.reminded_at >= REMIND_EVERY)
    upcoming = [p for p in plans if p.starts_at > now]
    done = [p for p in plans if p.starts_at <= now and p.status == ConcertPlan.AGREED]
    done.reverse()
    return {"upcoming": upcoming, "done": done}


def upcoming_count(conversation):
    return conversation.concerts.exclude(status=ConcertPlan.DECLINED).filter(starts_at__gt=timezone.now()).count()

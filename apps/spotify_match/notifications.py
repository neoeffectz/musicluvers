"""In-app notifications: new matches and new messages, shown under the bell."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince

from apps.events.realtime import push_to_user
from apps.users.models import CustomUser

from .models import Notification

PANEL_LIMIT = 20
PREVIEW_CHARS = 60


def unread_count(user):
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_all_read(user):
    Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())


def mark_conversation_read(user, conversation_id):
    Notification.objects.filter(
        user=user, kind="new_message", read_at__isnull=True,
        payload__conversation_id=str(conversation_id),
    ).update(read_at=timezone.now())


def _preview(body):
    body = " ".join(body.split())
    return body if len(body) <= PREVIEW_CHARS else body[:PREVIEW_CHARS - 1] + "…"


def notify_new_message(message, recipient):
    """One unread notification per conversation; later messages update it instead of piling up."""
    conversation_id = str(message.conversation_id)
    payload = {
        "other_user_id": str(message.sender_id),
        "conversation_id": conversation_id,
        "preview": _preview(message.body),
    }
    existing = Notification.objects.filter(
        user=recipient, kind="new_message", read_at__isnull=True,
        payload__conversation_id=conversation_id,
    ).first()
    if existing:
        payload["count"] = existing.payload.get("count", 1) + 1
        Notification.objects.filter(id=existing.id).update(payload=payload, created_at=timezone.now())
    else:
        payload["count"] = 1
        Notification.objects.create(user=recipient, kind="new_message", payload=payload)
    push_to_user(recipient.id, "notification", conversation_id=conversation_id)
    from apps.users import push
    sender = message.sender
    count = payload["count"]
    push.notify(recipient.id, sender.first_name or sender.username,
                payload["preview"] if count == 1 else f"{count} new messages · {payload['preview']}",
                url=f"/messages/?c={conversation_id}", tag=f"chat-{conversation_id}")


def items_for(user, limit=PANEL_LIMIT):
    notes = list(Notification.objects.filter(user=user)[:limit])
    ids = {n.payload.get("other_user_id") for n in notes if n.payload.get("other_user_id")}
    from apps.users import safety
    hidden = safety.blocked_ids(user)
    others = {str(u.id): u for u in CustomUser.objects.filter(id__in=ids).exclude(id__in=hidden).prefetch_related("photos")}
    items = []
    for n in notes:
        other = others.get(n.payload.get("other_user_id"))
        if other is None:
            continue  # the other account was deleted
        name = other.first_name or other.username
        if n.kind == "match_created":
            text = f"You matched with {name}"
            sub = "Say hi"
            link = reverse("messaging:open", args=[other.id])
        else:
            count = n.payload.get("count", 1)
            text = f"{name} sent you a message" if count == 1 else f"{name} sent you {count} messages"
            sub = n.payload.get("preview", "")
            link = f"{reverse('messaging:messages')}?c={n.payload.get('conversation_id')}"
        items.append({
            "kind": n.kind, "text": text, "sub": sub, "link": link,
            "photo_url": other.photo_url, "ago": _ago(n.created_at), "unread": n.read_at is None,
        })
    return items


def _ago(when):
    if timezone.now() - when < timedelta(minutes=1):
        return "just now"
    return f"{timesince(when).split(',')[0]} ago"

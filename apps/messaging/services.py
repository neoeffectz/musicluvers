from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.users.models import CustomUser
from apps.spotify_match.models import Match
from .models import Conversation, Message
from django.db import models, transaction
from apps.events import bus, catalog
from apps.spotify_match import notifications
from apps.users import safety


def get_or_create_conversation(user_a, user_b):
    """Only allowed if they're an actual mutual match."""
    a, b = sorted([user_a, user_b], key=lambda u: str(u.id))
    if safety.is_blocked_pair(a, b):
        raise PermissionError("Users are not matched.")
    is_matched = Match.objects.filter(user_a=a, user_b=b).exists()
    if not is_matched:
        raise PermissionError("Users are not matched.")
    conversation, _ = Conversation.objects.get_or_create(user_a=a, user_b=b)
    return conversation


def get_conversations_for(user):
    """Everything the inbox shows, in 2 queries total (not 4–5 per chat): the chats with both people,
    each chat's last message and unread count, then everyone's photos."""
    from types import SimpleNamespace
    latest = Message.objects.filter(conversation=models.OuterRef("pk")).order_by("-created_at", "-id")
    convos = (
        Conversation.objects.filter(models.Q(user_a=user) | models.Q(user_b=user))
        .select_related("user_a", "user_b")
        .prefetch_related("user_a__photos", "user_b__photos")
        .annotate(
            last_body=models.Subquery(latest.values("body")[:1]),
            last_at=models.Subquery(latest.values("created_at")[:1]),
            unread=models.Count("messages", filter=models.Q(messages__read_at__isnull=True) & ~models.Q(messages__sender=user)),
        )
        .order_by("-created_at")
    )
    hidden = safety.blocked_ids(user)  # a block hides the chat at once (the worker deletes it moments later)
    result = []
    for c in convos:
        other = get_other_user(c, user)
        if other.id in hidden:
            continue
        result.append({
            "conversation": c,
            "other_user": other,
            "last_message": SimpleNamespace(body=c.last_body, created_at=c.last_at) if c.last_at else None,
            "unread_count": c.unread,
        })
    return result





MAX_MESSAGE_CHARS = 2000


def send_message(conversation, sender, body):
    """Every send (web page and API) goes through here, so the rules live in one place."""
    if sender.id not in [conversation.user_a_id, conversation.user_b_id]:
        raise PermissionError("Not part of this conversation.")
    other = get_other_user(conversation, sender)
    if safety.is_blocked_pair(sender, other) or not _is_matched(sender, other):
        raise PermissionError("You can only message people you're matched with.")
    body = (body or "").strip()
    if not body:
        raise ValueError("Message can't be empty.")
    if len(body) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Messages can be up to {MAX_MESSAGE_CHARS} characters.")
    with transaction.atomic():
        message = Message.objects.create(conversation=conversation, sender=sender, body=body)
        bus.publish(catalog.MESSAGE_SENT, message_id=str(message.id), conversation_id=str(conversation.id))
        # Show it in the other person's open chat right away, without waiting for the background worker
        # (which may be busy). The worker still sends the bell + phone notification, and repeats this
        # delivery as a backup; the chat page ignores a message it already shows.
        transaction.on_commit(lambda: deliver_live(message))
    return message


def live_payload(message):
    return {
        "id": str(message.id),
        "sender_id": str(message.sender_id),
        "body": message.body,
        "created_at": message.created_at.strftime("%-I:%M %p"),
    }


def deliver_live(message):
    """Push a message to everyone with this chat open, and tell the recipient's other open pages (inbox
    list, Messages tab dot) straight away. Best effort: the background worker repeats the chat delivery
    and sends the bell + phone notification."""
    import logging
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from apps.events.realtime import push_to_user
    conversation = message.conversation
    recipient_id = conversation.user_b_id if conversation.user_a_id == message.sender_id else conversation.user_a_id
    try:
        async_to_sync(get_channel_layer().group_send)(
            f"conversation_{message.conversation_id}", {"type": "chat_message", "message": live_payload(message)})
        push_to_user(recipient_id, "new_message", conversation_id=str(message.conversation_id))
    except Exception:
        logging.getLogger(__name__).exception("Live delivery failed for message %s; the worker will retry", message.id)


def mark_read(conversation, reader):
    conversation.messages.filter(read_at__isnull=True).exclude(sender=reader).update(read_at=timezone.now())
    notifications.mark_conversation_read(reader, conversation.id)


def get_other_user(conversation, current_user):
    return conversation.user_b if conversation.user_a_id == current_user.id else conversation.user_a

PAGE_SIZE = 15

def get_messages(conversation, requesting_user, before=None, limit=PAGE_SIZE):
    if requesting_user.id not in [conversation.user_a_id, conversation.user_b_id]:
        raise PermissionError("Not part of this conversation.")

    qs = conversation.messages.select_related("sender").prefetch_related("liked_by").order_by("-created_at", "-id")

    if before:
        try:
            cursor = conversation.messages.filter(id=before).first()
        except ValidationError:
            cursor = None
        if cursor:
            qs = qs.filter(
                models.Q(created_at__lt=cursor.created_at) |
                models.Q(created_at=cursor.created_at, id__lt=cursor.id)
            )

    page = list(qs[:limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    page.reverse()  
    return page, has_more


def toggle_like(message, user):
    if user.id not in [message.conversation.user_a_id, message.conversation.user_b_id]:
        raise PermissionError("Not part of this conversation.")
    if message.liked_by.filter(id=user.id).exists():
        message.liked_by.remove(user)
        liked = False
    else:
        message.liked_by.add(user)
        liked = True
    return liked



def _is_matched(user_a, user_b):
    a, b = sorted([user_a, user_b], key=lambda u: str(u.id))
    return Match.objects.filter(user_a=a, user_b=b).exists()
"""Consumers for messaging-related events. Registered in MessagingConfig.ready()."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.events import bus, catalog
from apps.spotify_match import notifications
from .models import Conversation, Message


@bus.subscribe(catalog.MESSAGE_SENT)
def broadcast_message(payload):
    message = Message.objects.filter(id=payload["message_id"]).first()
    if message is None:
        return
    # Backup delivery (the request already pushed it live; the chat page drops duplicates).
    from .services import live_payload
    async_to_sync(get_channel_layer().group_send)(
        f"conversation_{message.conversation_id}", {"type": "chat_message", "message": live_payload(message)})
    conversation = message.conversation
    recipient = conversation.user_b if conversation.user_a_id == message.sender_id else conversation.user_a
    notifications.notify_new_message(message, recipient)


@bus.subscribe(catalog.MATCH_REMOVED)
def delete_conversation_for_removed_match(payload):
    Conversation.objects.filter(user_a_id=payload["user_a_id"], user_b_id=payload["user_b_id"]).delete()

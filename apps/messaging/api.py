from uuid import UUID
from ninja import Router
from ninja.security import django_auth
from ninja.errors import HttpError
from django.shortcuts import get_object_or_404

from . import services
from .models import Conversation
from .schemas import ConversationOut, MessageOut, SendMessageIn, OpenConversationIn
from apps.users.models import CustomUser

router = Router(tags=["messaging"])


@router.get("/conversations", response=list[ConversationOut], auth=django_auth)
def list_conversations(request):
    user = request.auth
    items = services.get_conversations_for(user)
    return [
        {
            "conversation_id": str(item["conversation"].id),
            "other_user": {
                "user_id": str(item["other_user"].id),
                "display_name": item["other_user"].first_name or item["other_user"].username,
                "profile_image_url": item["other_user"].spotify_profile_image_url,
            },
            "last_message": item["last_message"].body if item["last_message"] else None,
            "last_message_at": item["last_message"].created_at if item["last_message"] else None,
            "unread_count": item["unread_count"],
        }
        for item in items
    ]


@router.post("/conversations/open", response=ConversationOut, auth=django_auth)
def open_conversation(request, payload: OpenConversationIn):
    user = request.auth
    other_user = get_object_or_404(CustomUser, id=payload.other_user_id)
    try:
        conversation = services.get_or_create_conversation(user, other_user)
    except PermissionError:
        raise HttpError(403, "You can only message users you've matched with.")

    other = services.get_other_user(conversation, user)
    last = conversation.messages.last()
    return {
        "conversation_id": str(conversation.id),
        "other_user": {
            "user_id": str(other.id),
            "display_name": other.first_name or other.username,
            "profile_image_url": other.spotify_profile_image_url,
        },
        "last_message": last.body if last else None,
        "last_message_at": last.created_at if last else None,
        "unread_count": 0,
    }


@router.get("/conversations/{conversation_id}/messages", response=list[MessageOut], auth=django_auth)
def get_messages(request, conversation_id: UUID):
    user = request.auth
    conversation = get_object_or_404(Conversation, id=conversation_id)
    try:
        messages, has_more = services.get_messages(conversation, user)  
    except PermissionError:
        raise HttpError(403, "Not part of this conversation.")

    services.mark_read(conversation, user)

    return [
        {
            "message_id": str(m.id),
            "sender_id": str(m.sender_id),
            "body": m.body,
            "created_at": m.created_at,
            "is_mine": m.sender_id == user.id,
        }
        for m in messages
    ]


@router.post("/conversations/{conversation_id}/messages", response=MessageOut, auth=django_auth)
def send_message(request, conversation_id: UUID, payload: SendMessageIn):
    user = request.auth
    conversation = get_object_or_404(Conversation, id=conversation_id)
    body = payload.body.strip()
    if not body:
        raise HttpError(400, "Message body cannot be empty.")

    try:
        message = services.send_message(conversation, user, body)
    except PermissionError as e:
        raise HttpError(403, str(e))
    except ValueError as e:
        raise HttpError(400, str(e))

    return {
        "message_id": str(message.id),
        "sender_id": str(message.sender_id),
        "body": message.body,
        "created_at": message.created_at,
        "is_mine": True,
    }

@router.get("/conversations/{conversation_id}/messages/older", response=list[MessageOut], auth=django_auth)
def get_older_messages(request, conversation_id: UUID, before: str):
    user = request.auth
    conversation = get_object_or_404(Conversation, id=conversation_id)
    try:
        messages, has_more = services.get_messages(conversation, user, before=before)
    except PermissionError:
        raise HttpError(403, "Not part of this conversation.")
    return [
        {
            "message_id": str(m.id),
            "sender_id": str(m.sender_id),
            "body": m.body,
            "created_at": m.created_at,
            "is_mine": m.sender_id == user.id,
        }
        for m in messages
    ]


@router.post("/messages/{message_id}/like", auth=django_auth)
def toggle_like(request, message_id: UUID):
    from .models import Message
    message = get_object_or_404(Message, id=message_id)
    try:
        liked = services.toggle_like(message, request.auth)
    except PermissionError:
        raise HttpError(403, "Not part of this conversation.")
    return {"liked": liked}
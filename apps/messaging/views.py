from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.http import HttpResponse, HttpResponseForbidden
from apps.users.models import CustomUser
from . import services
from .models import *
from apps.spotify_match import services as match_services
from apps.users import analytics


import logging
logger = logging.getLogger(__name__)


@login_required(login_url="users:home")
@ensure_csrf_cookie
def messages_page(request):
    conversations = services.get_conversations_for(request.user)
    active_conversation = None
    thread_messages = []
    other_user = None

    convo_id = request.GET.get("c")
    if convo_id:
        import uuid
        try:
            uuid.UUID(convo_id)
        except ValueError:
            return redirect("messaging:messages")  # a mangled link shouldn't crash the page
        active_conversation = get_object_or_404(Conversation, id=convo_id)
        if request.user.id not in [active_conversation.user_a_id, active_conversation.user_b_id]:
            return redirect("messaging:messages")
        if not services._is_matched(request.user, services.get_other_user(active_conversation, request.user)):
                return redirect("messaging:messages")
        thread_messages, has_more_older = services.get_messages(active_conversation, request.user)
        services.mark_read(active_conversation, request.user)
        other_user = services.get_other_user(active_conversation, request.user)
    else:
        has_more_older = False

    context = {
    "conversations": conversations,
    "active_conversation": active_conversation,
    "other_user": other_user,
    "thread_messages": thread_messages,
    "has_more_older": has_more_older,
}
    context.update(match_services.get_sidebar_context(request.user))
    return render(request, "messaging/messages.html", context)

@login_required(login_url="users:home")
def open_conversation(request, other_user_id):
    other_user = get_object_or_404(CustomUser, id=other_user_id)
    try:
        conversation = services.get_or_create_conversation(request.user, other_user)
    except PermissionError:
        return redirect("messaging:messages")
    return redirect(f"/messages/?c={conversation.id}")





@login_required(login_url="users:home")
@require_POST
def send_message_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if request.user.id not in [conversation.user_a_id, conversation.user_b_id]:
        return redirect("messaging:messages")
    if not services._is_matched(request.user, services.get_other_user(conversation, request.user)):
        return HttpResponseForbidden("You can't message this person anymore.", content_type="text/plain")
    body = request.POST.get("body", "").strip()
    if body:
        try:
            services.send_message(conversation, request.user, body)
        except PermissionError:
            return HttpResponseForbidden("You can't message this person anymore.", content_type="text/plain")
        except ValueError as e:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest(str(e))

    thread_messages, has_more_older = services.get_messages(conversation, request.user)
    response = render(request, "messaging/partials/_thread_messages.html", {
        "thread_messages": thread_messages,
        "has_more_older": has_more_older,
        "active_conversation": conversation,
        "request": request,
    })
    if body:
        analytics.track_response(response, "message_sent")  # the event only — never the text
    return response

@login_required(login_url="users:home")
def thread_partial(request, conversation_id):
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if request.user.id not in [conversation.user_a_id, conversation.user_b_id]:
        return redirect("messaging:messages")
    if not services._is_matched(request.user, services.get_other_user(conversation, request.user)):
            return redirect("messaging:messages")
    thread_messages, has_more_older = services.get_messages(conversation, request.user)
    services.mark_read(conversation, request.user)
    other_user = services.get_other_user(conversation, request.user)
    return render(request, "messaging/partials/_thread_panel.html", {
        "active_conversation": conversation,
        "other_user": other_user,
        "thread_messages": thread_messages,
        "has_more_older": has_more_older,
        "request": request,
    })

@login_required(login_url="users:home")
def conversations_list_partial(request):
    conversations = services.get_conversations_for(request.user)
    return render(request, "messaging/partials/_conversations_list.html", {
        "conversations": conversations,
    })

@login_required(login_url="users:home")
def load_older_messages(request, conversation_id):
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if request.user.id not in [conversation.user_a_id, conversation.user_b_id]:
        return redirect("messaging:messages")
    before = request.GET.get("before")
    thread_messages, has_more_older = services.get_messages(conversation, request.user, before=before)
    return render(request, "messaging/partials/_older_messages.html", {
        "thread_messages": thread_messages,
        "has_more_older": has_more_older,
        "conversation_id": conversation.id,
    })

@login_required(login_url="users:home")
@require_POST
def toggle_like_view(request, message_id):
    message = get_object_or_404(Message, id=message_id)
    try:
        liked = services.toggle_like(message, request.user)
    except PermissionError:
        return redirect("messaging:messages")
    return render(request, "messaging/partials/_message_bubble.html", {
        "msg": message,
        "request": request,
    })

@login_required(login_url="users:home")
@require_POST
def mark_read_view(request, conversation_id):
    """Called by the Messages page when a message arrives in the chat that's open on screen."""
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if request.user.id not in [conversation.user_a_id, conversation.user_b_id]:
        return HttpResponseForbidden()
    services.mark_read(conversation, request.user)
    return HttpResponse(status=204)

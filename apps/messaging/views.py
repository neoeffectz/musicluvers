from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
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
    if request.GET.get("part") == "messages":   # just the messages (a concert card arrived): tabs stay as they are
        return render(request, "messaging/partials/_thread_messages.html", {
            "active_conversation": conversation, "thread_messages": thread_messages,
            "has_more_older": has_more_older, "request": request,
        })
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


# OUR CONCERTS

def _concert_panel(request, conversation, error=None, form=None, status=200):
    from . import concerts
    other = services.get_other_user(conversation, request.user)
    return render(request, "messaging/partials/_concerts.html", {
        **concerts.board(conversation, request.user), "active_conversation": conversation, "other_user": other,
        "error": error, "form": form or {}, "request": request,
    }, status=status)


def _concerts_off():
    from . import concerts
    return None if concerts.is_enabled() else HttpResponse("Not available.", status=404, content_type="text/plain")


@login_required(login_url="users:home")
def concerts_panel(request, conversation_id):
    if _concerts_off():
        return _concerts_off()
    conversation = get_object_or_404(Conversation, id=conversation_id)
    try:
        return _concert_panel(request, conversation)
    except PermissionError:
        return HttpResponseForbidden("You can't see this.", content_type="text/plain")


@login_required(login_url="users:home")
@require_POST
def concert_add(request, conversation_id):
    if _concerts_off():
        return _concerts_off()
    from datetime import datetime
    from . import concerts
    conversation = get_object_or_404(Conversation, id=conversation_id)
    form = {k: request.POST.get(k, "") for k in ("title", "venue", "date", "time", "ticket_url")}
    try:
        starts_at = datetime.strptime(f"{form['date']} {form['time'] or '19:00'}", "%Y-%m-%d %H:%M")
    except ValueError:
        starts_at = None
    try:
        concerts.propose(conversation, request.user, form["title"], form["venue"], starts_at, form["ticket_url"])
    except ValueError as e:
        return _concert_panel(request, conversation, error=str(e), form=form, status=400)
    except PermissionError:
        return HttpResponseForbidden("You can't plan in this chat.", content_type="text/plain")
    response = _concert_panel(request, conversation)
    return analytics.track_response(response, "concert_proposed")


@login_required(login_url="users:home")
@require_POST
def concert_action(request, plan_id, action):
    if _concerts_off():
        return _concerts_off()
    from .models import ConcertPlan
    from . import concerts
    plan = get_object_or_404(ConcertPlan.objects.select_related("conversation", "proposed_by"), id=plan_id)
    try:
        if action in ("agree", "decline"):
            concerts.respond(plan, request.user, agree=action == "agree")
        elif action == "remove":
            concerts.remove(plan, request.user)
        elif action == "remind":
            concerts.remind(plan, request.user)
        elif action == "ticket":
            concerts.toggle_ticket(plan, request.user)
        elif action == "love":
            concerts.toggle_love(plan, request.user)
        else:
            return HttpResponseBadRequest("Unknown action.", content_type="text/plain")
    except PermissionError as e:
        return HttpResponseForbidden(str(e), content_type="text/plain")
    except ValueError as e:
        return HttpResponseBadRequest(str(e), content_type="text/plain")
    if request.POST.get("from") == "card":   # answered from the card in the chat
        plan.refresh_from_db()
        response = render(request, "messaging/partials/_concert_card.html", {"plan": plan, "request": request})
    else:
        response = _concert_panel(request, plan.conversation)
    if action == "agree":
        analytics.track_response(response, "concert_agreed")
    return response

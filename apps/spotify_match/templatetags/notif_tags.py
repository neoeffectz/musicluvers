from django import template

from .. import notifications

register = template.Library()


@register.simple_tag(takes_context=True)
def unread_notifications(context):
    """Unread count for the bell, worked out once per page (both bells share it) instead of
    two extra requests after every page load."""
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return 0
    if not hasattr(request, "_unread_notifications"):
        request._unread_notifications = notifications.unread_count(request.user)
    return request._unread_notifications


@register.simple_tag(takes_context=True)
def has_unread_messages(context):
    """True when someone sent this user a message they haven't opened (dot on the Messages tab)."""
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return False
    if not hasattr(request, "_has_unread_messages"):
        from django.db.models import Q
        from apps.messaging.models import Message
        user = request.user
        request._has_unread_messages = Message.objects.filter(
            Q(conversation__user_a=user) | Q(conversation__user_b=user), read_at__isnull=True,
        ).exclude(sender=user).exists()
    return request._has_unread_messages

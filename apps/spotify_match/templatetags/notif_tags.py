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

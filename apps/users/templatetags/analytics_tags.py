from django import template

from apps.users import analytics

register = template.Library()


@register.simple_tag(takes_context=True)
def pending_analytics(context):
    """Events queued by a view before a redirect; sent once from this page."""
    request = context.get("request")
    return analytics.pop_pending(request) if request is not None else []

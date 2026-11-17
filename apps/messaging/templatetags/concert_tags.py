from django import template

from apps.messaging import concerts

register = template.Library()


@register.simple_tag
def concerts_on():
    """Whether "Our Concerts" is switched on (settings.CONCERTS_ENABLED)."""
    return concerts.is_enabled()


@register.simple_tag
def upcoming_concerts(conversation):
    """Number shown on the "Our Concerts" tab."""
    return concerts.upcoming_count(conversation) if conversation else 0

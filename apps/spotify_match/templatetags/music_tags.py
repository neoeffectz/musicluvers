from django import template

from apps.spotify_match import services

register = template.Library()


@register.simple_tag
def music_answers(user):
    """A person's question answers in profile order (anthem first)."""
    return services.answered_prompts(user)

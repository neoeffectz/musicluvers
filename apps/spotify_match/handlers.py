"""Consumers for taste and match events. Registered in SpotifyMatchConfig.ready()."""
from apps.events import bus, catalog
from apps.events.realtime import push_to_user
from apps.users import push
from apps.users.models import CustomUser

from . import lastfm, services
from .models import Notification, SOURCE_LASTFM


@bus.subscribe(catalog.TASTE_SOURCE_CONNECTED)
def pull_taste_from_source(payload):
    user = CustomUser.objects.get(id=payload["user_id"])
    if payload["source"] == SOURCE_LASTFM:
        lastfm.sync_lastfm_taste(user)
    else:
        return
    services.mark_taste_synced(user)
    bus.publish(catalog.TASTE_PROFILE_UPDATED, user_id=str(user.id), source=payload["source"])


@bus.subscribe(catalog.TASTE_PROFILE_UPDATED)
def recompute_match_scores(payload):
    user = CustomUser.objects.get(id=payload["user_id"])
    services.recompute_scores_for_user(user)
    bus.publish(catalog.MATCH_SCORES_UPDATED, user_id=str(user.id))


@bus.subscribe(catalog.MATCH_SCORES_UPDATED)
def tell_user_discover_changed(payload):
    """Tell this user and everyone who can now see them, so open Discover screens refresh without a reload."""
    push_to_user(payload["user_id"], "discover_updated")
    user = CustomUser.objects.filter(id=payload["user_id"]).first()
    if user is None:
        return
    for other_id in services.discover_audience(user):
        push_to_user(other_id, "discover_updated")


@bus.subscribe(catalog.MATCH_CREATED)
def notify_both_users_of_match(payload):
    users = {str(u.id): u for u in CustomUser.objects.filter(id__in=[payload["user_a_id"], payload["user_b_id"]])}
    pairs = [(payload["user_a_id"], payload["user_b_id"]), (payload["user_b_id"], payload["user_a_id"])]
    for user_id, other_id in pairs:
        other = users[other_id]
        Notification.objects.create(user_id=user_id, kind="match_created", payload={"other_user_id": other_id})
        name = other.first_name or other.username
        push.notify(user_id, "It's a match! 🎵", f"You and {name} like each other's sound. Say hi!",
                    url=f"/messages/open/{other_id}/", tag=f"match-{other_id}")
        push_to_user(
            user_id, "match_created",
            other_user_id=other_id,
            other_user_name=other.first_name or other.username,
        )

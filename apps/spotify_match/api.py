# apps/spotify_match/api.py
from uuid import UUID
from ninja import Router
from ninja.security import django_auth
from . import services
from .schemas import TasteProfileOut, DiscoveryCardOut, SwipeIn, SwipeOut, MatchOut, UserProfileOut, SyncStartedOut
from ninja.errors import HttpError
from django.shortcuts import get_object_or_404
from apps.users.models import CustomUser

router = Router(tags=["match"])


@router.get("/taste/me", response=TasteProfileOut, auth=django_auth)
def my_taste(request):
    user = request.auth
    stats = services.taste_stats(user)
    return {
        "display_name": user.first_name or user.username,
        "profile_image_url": user.spotify_profile_image_url,
        **stats,
    }


@router.post("/taste/sync", response=SyncStartedOut, auth=django_auth)
def resync_taste(request):
    sources = services.request_taste_resync(request.auth)
    return {"status": "syncing" if sources else "no_source_connected"}


@router.get("/discover", response=list[DiscoveryCardOut], auth=django_auth)
def discover(request):
    feed = services.get_discovery_feed(request.auth)
    return [
        {
            "user_id": str(item["user"].id),
            "display_name": item["user"].first_name or item["user"].username,
            "country": item["user"].spotify_country,
            "profile_image_url": item["user"].spotify_profile_image_url,
            "score": item["score"],
            "shared_artists": item["shared_artists"],
            "shared_tracks": item["shared_tracks"],
        }
        for item in feed
    ]


@router.post("/swipe", response=SwipeOut, auth=django_auth)
def swipe(request, payload: SwipeIn):
    try:
        result = services.record_swipe(request.auth, payload.to_user_id, payload.liked)
    except ValueError as e:
        raise HttpError(400, str(e))
    return {
        "matched": result["matched"],
        "matched_user_name": result["matched_user"].first_name if result["matched"] else None,
    }


@router.get("/matches", response=list[MatchOut], auth=django_auth)
def matches(request):
    return [
        {
            "user_id": str(u.id),
            "display_name": u.first_name or u.username,
            "country": u.spotify_country,
            "profile_image_url": u.spotify_profile_image_url,
        }
        for u in services.get_matches(request.auth)
    ]



@router.get("/user/{user_id}", response=UserProfileOut, auth=django_auth)
def user_profile(request, user_id: UUID):
    from apps.users.models import CustomUser
    other_user = get_object_or_404(CustomUser, id=user_id)
    card = services.get_user_profile_view(request.auth, other_user)
    if card is None:
        raise HttpError(403, "Not matched with this user.")
    return {
        "user_id": str(card["user"].id),
        "display_name": card["user"].first_name or card["user"].username,
        "country": card["user"].spotify_country,
        "profile_image_url": card["user"].spotify_profile_image_url,
        "score": card["score"],
        "shared_artists": card["shared_artists"],
        "shared_tracks": card["shared_tracks"],
        "top_artists": card["top_artists"],
        "top_tracks": card["top_tracks"],
    }


@router.post("/unmatch/{user_id}", auth=django_auth)
def unmatch(request, user_id: UUID):
    from apps.users.models import CustomUser
    other_user = get_object_or_404(CustomUser, id=user_id)
    services.unmatch(request.auth, other_user)
    return {"success": True}
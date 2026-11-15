from ninja import NinjaAPI

api = NinjaAPI(
    title="SoundMatch API",
    version="1.0.0",
    description="Powers the SoundMatch dashboard — usable by any frontend (web, React, mobile).",
)

from apps.users.api import router as users_router
from apps.spotify_match.api import router as match_router
from apps.messaging.api import router as messaging_router


api.add_router("/users/", users_router)
api.add_router("/match/", match_router)
api.add_router("/messages/", messaging_router)

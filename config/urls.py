from django.contrib import admin
from django.templatetags.static import static
from django.urls import path, include
from django.views.generic.base import RedirectView
from config.api import api


class FaviconRedirect(RedirectView):
    """Works out the (hashed, in production) icon URL per request, not at import time —
    resolving it while urls.py loads crashes the whole site when the static manifest isn't built yet."""
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        return static("favicon.svg")

urlpatterns = [
    path("favicon.ico", FaviconRedirect.as_view()),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", include("apps.users.urls")),
    path("", include("apps.spotify_match.urls")),
    path("messages/", include("apps.messaging.urls")),
    path("lounge/", include("apps.lounge.urls")),
]
from .base import *
DEBUG = True
ALLOWED_HOSTS = ["*"]
# Phone testing through an ngrok https tunnel: trust its addresses for form posts (CSRF),
# and believe ngrok's "this was https" header so secure-only browser features (GPS) behave.
CSRF_TRUSTED_ORIGINS = [
    "https://*.ngrok-free.app",
    "https://*.ngrok-free.dev",
    "https://*.ngrok.app",
    "https://*.ngrok.io",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Login lockouts (django-axes) while testing through ngrok:
# - ngrok makes every visitor look like 127.0.0.1, so read the real phone IP from X-Forwarded-For
#   (only trustworthy because this is dev; production keeps the strict defaults; no ipware dependency needed);
# - lock a username+device pair, not the whole IP, so one person's typos can't lock everyone out;
# - unlock by itself after 15 minutes instead of needing an admin.
from datetime import timedelta  # noqa: E402



def _client_ip(request):
    """ngrok puts the phone's real address first in X-Forwarded-For; direct visits fall back to REMOTE_ADDR."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")


AXES_CLIENT_IP_CALLABLE = _client_ip
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)

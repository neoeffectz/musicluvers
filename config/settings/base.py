import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
REDIS_URL = env("REDIS_URL")
DEBUG = env.bool("DEBUG", default=False)

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # staticfiles BEFORE cloudinary_storage: Cloudinary only stores uploaded photos here, and its own
    # collectstatic skips images (e.g. admin/img/*.svg), which made the production static build fail.
    "django.contrib.staticfiles",
    "cloudinary_storage",
    "cloudinary",
    "axes",
    "apps.users",
    "apps.spotify_match",
    "apps.messaging",
    "apps.events",
    "apps.lounge",
    "channels",
]

AUTH_USER_MODEL = "users.CustomUser"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.users.middleware.HtmxRedirectMiddleware",  # expired login during a tap -> full login page, not inside a box
    "apps.users.middleware.FinishSignupMiddleware",  # location + gender + birthday required before using the app
]

# A form left open too long (or after logging in elsewhere) fails Django's security check. Show a friendly page.
CSRF_FAILURE_VIEW = "apps.users.views.csrf_failure"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

DATABASES = {"default": env.db()}
DATABASES["default"]["CONN_MAX_AGE"] = 600

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "apps.users.context_processors.app_flags",
    ]},
}]

# Times on screen (chat, "5 minutes ago", ...) are shown in Lagos time. Without this Django used its
# default, America/Chicago, 7 hours behind. Stored times are exact moments, so nothing needs migrating.
TIME_ZONE = env("TIME_ZONE", default="Africa/Lagos")

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": env("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": env("CLOUDINARY_API_KEY"),
    "API_SECRET": env("CLOUDINARY_API_SECRET"),
}
DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"

SPOTIFY_CLIENT_ID = env("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = env("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = env("SPOTIFY_REDIRECT_URI", default="")  # unused since Spotify login was removed; kept so old .env files still load
FERNET_KEY = env("FERNET_KEY")
LASTFM_API_KEY = env("LASTFM_API_KEY", default="")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",  # was channels_redis.pubsub.RedisPubSubChannelLayer
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

CELERY_BROKER_URL = REDIS_URL
# How long the worker waits on Redis for a job before asking again. Redis answers the moment a job
# arrives, so a longer wait doesn't slow anything down; it only means fewer requests to Upstash (whose
# free plan counts every request). Override with CELERY_POLLING_INTERVAL.
_poll = env.int("CELERY_POLLING_INTERVAL", default=30)
CELERY_BROKER_TRANSPORT_OPTIONS = {"polling_interval": _poll} if _poll else {}
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
# Safety net for jobs that failed to queue (rare: Redis down for a moment). Normal jobs run straight away.
# Not every minute: the site is kept awake all day (uptime monitor), and a check every minute would keep
# the Neon database from ever sleeping (free plan: ~400 awake hours a month) and use up Upstash requests.
from celery.schedules import crontab  # noqa: E402

CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "sweep-outbox": {"task": "apps.events.tasks.sweep_outbox_task",
                     "schedule": float(env.int("OUTBOX_SWEEP_SECONDS", default=1800))},
    # Friday 5pm Lagos: "The Lounge is open" phone notification (once a week, costs nothing otherwise)
    "lounge-open": {"task": "apps.lounge.tasks.announce_lounge_open",
                    "schedule": crontab(minute=0, hour=17, day_of_week="fri")},
}

# The Friday Lounge opens Fridays 5-6pm. LOUNGE_ALWAYS_OPEN=True opens it any day (for trying it out).
LOUNGE_ALWAYS_OPEN = env.bool("LOUNGE_ALWAYS_OPEN", default=False)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        
        'django.db.backends': {
    'handlers': ['console'],
    'level': 'INFO',   
    'propagate': False,
},
    },
}
# ---------------------------------------------------------------------------
# Email (password reset, email verification, report alerts) through Resend.
# Same pattern as GoCab: a valid RESEND_API_KEY alone isn't enough — Resend only
# delivers to other people once the sending domain is verified. So real sending
# is switched on by a SECOND flag. Until then everything is printed to the server
# log instead, and the "Forgot password" link / verify-email banner stay hidden.
#
# When musicluvers.site is bought and shows "Verified" in Resend's dashboard, add to .env:
#   RESEND_API_KEY=re_...
#   RESEND_DOMAIN_VERIFIED=True
#   DEFAULT_FROM_EMAIL=Music Luvers <hello@musicluvers.site>
#   ADMIN_NOTIFICATION_EMAIL=you@yourmail.com   (where new reports go)
# ---------------------------------------------------------------------------
RESEND_API_KEY = env("RESEND_API_KEY", default="")
RESEND_DOMAIN_VERIFIED = env.bool("RESEND_DOMAIN_VERIFIED", default=False)
EMAIL_ENABLED = bool(RESEND_API_KEY and RESEND_DOMAIN_VERIFIED)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Music Luvers <hello@musicluvers.site>")
ADMIN_NOTIFICATION_EMAIL = env("ADMIN_NOTIFICATION_EMAIL", default="")
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2  # reset links work for 2 hours
EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24 * 3  # verify links work for 3 days

if EMAIL_ENABLED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.resend.com"
    EMAIL_PORT = 587
    EMAIL_HOST_USER = "resend"
    EMAIL_HOST_PASSWORD = RESEND_API_KEY
    EMAIL_USE_TLS = True
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Web push (phone notifications). Keys live in .env; without them push is simply off.
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_SUBJECT = env("VAPID_SUBJECT", default="mailto:hello@musicluvers.site")
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Sessions: read from the in-process cache first and only fall back to the database on a miss.
# Saves a database round trip on almost every request (each one costs ~0.3s from Nigeria to Neon in the US).
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# ---------------------------------------------------------------------------
# Visitor analytics (PostHog). The project key is public by design (it's sent to every browser).
# Empty = off: local dev has no key, so testing never counts as visitors. Set in render.yaml.
POSTHOG_KEY = env("POSTHOG_KEY", default="")
POSTHOG_HOST = env("POSTHOG_HOST", default="https://us.i.posthog.com")
# Account ids (comma-separated) that are never tracked: the team testing the app.
POSTHOG_IGNORE_USER_IDS = set(env.list("POSTHOG_IGNORE_USER_IDS", default=[]))



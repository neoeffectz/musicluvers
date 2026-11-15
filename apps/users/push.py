"""Phone notifications (Web Push). Works on Android/desktop browsers, and on iPhone (iOS 16.4+) once the
site is added to the Home Screen. Sending happens on the Celery worker."""
import json
import logging
from urllib.parse import urlparse

from django.conf import settings

from .models import PushSubscription

logger = logging.getLogger(__name__)
ALLOWED_PUSH_HOSTS = (  # only real browser push services, never an arbitrary URL from a client
    "fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
    "web.push.apple.com", "notify.windows.com",
)


def _allowed(endpoint):
    host = urlparse(endpoint).hostname or ""
    return urlparse(endpoint).scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_PUSH_HOSTS)


def save_subscription(user, data, user_agent=""):
    endpoint = (data or {}).get("endpoint", "")
    keys = (data or {}).get("keys") or {}
    if not (endpoint and keys.get("p256dh") and keys.get("auth")) or not _allowed(endpoint):
        raise ValueError("That isn't a valid notification subscription.")
    sub, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={"user": user, "p256dh": keys["p256dh"][:255], "auth": keys["auth"][:255], "user_agent": user_agent[:255]},
    )
    return sub


def remove_subscription(user, endpoint):
    PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()


def notify(user_id, title, body, url="/dashboard/", tag=None):
    """Queue a phone notification for every device this person turned notifications on for."""
    if not settings.PUSH_ENABLED or not PushSubscription.objects.filter(user_id=user_id).exists():
        return
    from .tasks import send_push_task
    send_push_task.delay(str(user_id), {"title": title, "body": body, "url": url, "tag": tag or url})


def deliver(user_id, payload, only_ids=None):
    """Runs on the worker. Removes subscriptions the push service says are gone (phone uninstalled, etc.).
    Returns ids of subscriptions that failed for a temporary reason (network), so the job can retry just those."""
    import requests
    from pywebpush import WebPushException, webpush
    retry = []
    subs = PushSubscription.objects.filter(user_id=user_id)
    if only_ids is not None:
        subs = subs.filter(id__in=only_ids)
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=json.dumps(payload),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=60 * 60 * 24,
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                sub.delete()
            elif status in (429, 500, 502, 503, 504):
                retry.append(str(sub.id))
            else:
                logger.warning("Push to %s failed (%s): %s", sub.id, status, exc)
        except requests.RequestException as exc:  # timeouts, dropped connections
            logger.warning("Push to %s hit a network problem, will retry: %s", sub.id, exc)
            retry.append(str(sub.id))
        except Exception:
            logger.exception("Push to %s failed", sub.id)
    return retry

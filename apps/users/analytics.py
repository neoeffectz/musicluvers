"""Product analytics (PostHog). Everything here is a no-op unless POSTHOG_KEY is set, which only
production does, so local testing never shows up as visitors.

Only event names and small flags are sent (e.g. liked=True). Never send names, emails, messages,
song answers or locations. People are identified by their random account id only.
"""
import json

from django.conf import settings

SESSION_KEY = "ml_track"


def enabled():
    return bool(settings.POSTHOG_KEY)


def track(request, event, **props):
    """For views that redirect: the event is sent from the next page that loads."""
    if not enabled():
        return
    queue = request.session.get(SESSION_KEY, [])
    queue.append({"event": event, "props": props})
    request.session[SESSION_KEY] = queue[-10:]


def pop_pending(request):
    session = getattr(request, "session", None)
    if not enabled() or session is None or SESSION_KEY not in session:
        return []
    return session.pop(SESSION_KEY)


def track_response(response, event, **props):
    """For htmx partial responses: the browser sends the event when the response arrives."""
    if not enabled():
        return response
    raw = response.get("HX-Trigger")
    try:
        triggers = json.loads(raw) if raw else {}
    except ValueError:  # plain event name, e.g. "notificationsChanged"
        triggers = {raw: None}
    if not isinstance(triggers, dict):
        triggers = {}
    bucket = triggers.get("ml:track") or {"events": []}
    bucket["events"].append({"event": event, "props": props})
    triggers["ml:track"] = bucket
    response["HX-Trigger"] = json.dumps(triggers)
    return response

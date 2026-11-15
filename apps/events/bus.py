import logging
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent, ProcessedEvent

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

_handlers = defaultdict(list)


def subscribe(event_name):
    def register(func):
        _handlers[event_name].append(func)
        return func
    return register


def handler_key(func):
    return f"{func.__module__}.{func.__qualname__}"


def publish(name, **payload):
    """Record the event in the outbox as part of the caller's transaction.

    The event row commits or rolls back together with the state change that
    caused it. Only after commit is it handed to Celery; if that hand-off is
    lost, the outbox sweeper re-sends it.
    """
    event = OutboxEvent.objects.create(name=name, payload=payload)
    transaction.on_commit(lambda: _enqueue(event.id))
    return event


def _enqueue(event_id):
    from .tasks import dispatch_event_task
    try:
        dispatch_event_task.delay(str(event_id))
    except Exception:
        logger.exception("Could not enqueue event %s; the sweeper will retry it", event_id)


def dispatch(event_id):
    """Run every handler for the event once. Returns True when all succeeded.

    Delivery is at-least-once, so each handler's success is recorded in
    ProcessedEvent (in the same transaction as the handler's own writes) and
    a redelivered event skips handlers that already ran.
    """
    event = OutboxEvent.objects.filter(id=event_id).first()
    if event is None or event.status != OutboxEvent.PENDING:
        return True

    errors = []
    for func in _handlers[event.name]:
        key = handler_key(func)
        if ProcessedEvent.objects.filter(event=event, handler=key).exists():
            continue
        try:
            with transaction.atomic():
                func(event.payload)
                ProcessedEvent.objects.create(event=event, handler=key)
        except Exception as e:
            logger.exception("Handler %s failed for event %s", key, event.id)
            errors.append(f"{key}: {e!r}")

    event.attempts += 1
    if errors:
        event.last_error = "\n".join(errors)
        if event.attempts >= MAX_ATTEMPTS:
            event.status = OutboxEvent.FAILED
    else:
        event.status = OutboxEvent.DONE
        event.last_error = ""
        event.processed_at = timezone.now()
    event.save(update_fields=["attempts", "last_error", "status", "processed_at", "updated_at"])
    return not errors

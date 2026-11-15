from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from . import bus
from .models import OutboxEvent

STALE_AFTER = timedelta(seconds=60)


@shared_task(bind=True, max_retries=bus.MAX_ATTEMPTS - 1)
def dispatch_event_task(self, event_id):
    if not bus.dispatch(event_id):
        raise self.retry(countdown=min(5 * 2 ** self.request.retries, 300))


@shared_task
def sweep_outbox_task():
    """Re-send events that were never picked up (e.g. the web process died
    between committing and enqueueing, or Redis was briefly down)."""
    cutoff = timezone.now() - STALE_AFTER
    stale = OutboxEvent.objects.filter(status=OutboxEvent.PENDING, updated_at__lt=cutoff)
    for event_id in stale.values_list("id", flat=True)[:500]:
        dispatch_event_task.delay(str(event_id))

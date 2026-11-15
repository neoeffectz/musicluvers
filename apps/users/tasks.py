"""Background jobs for user profiles."""
from celery import shared_task

RETRY_DELAYS = [20, 60, 180, 600]  # seconds: covers brief Wi-Fi/phone network drops


@shared_task(bind=True, max_retries=len(RETRY_DELAYS))
def fill_city_name(self, user_id):
    """GPS was saved but the city-name lookup failed (network blip): keep trying in the background,
    so cards show "Lagos, Nigeria" instead of nothing. People never type their city."""
    from . import geocode
    from .models import CustomUser

    user = CustomUser.objects.filter(id=user_id).first()
    if user is None or user.location or not user.has_position:
        return
    city = geocode.city_for(user.latitude, user.longitude)
    if city:
        CustomUser.objects.filter(id=user.id, location="").update(location=city)
        return
    if self.request.retries < len(RETRY_DELAYS):
        raise self.retry(countdown=RETRY_DELAYS[self.request.retries])


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_task(self, to, subject, text, html=None):
    """Retries a few times if Resend/the network hiccups."""
    from .emails import deliver
    try:
        deliver(to, subject, text, html)
    except Exception as exc:
        raise self.retry(exc=exc)


PUSH_RETRY_DELAYS = [30, 120, 300]  # seconds


@shared_task(bind=True, max_retries=len(PUSH_RETRY_DELAYS))
def send_push_task(self, user_id, payload, only_ids=None):
    """Network trouble (common on this Wi-Fi and on phones) retries only the phones that failed."""
    from .push import deliver
    failed = deliver(user_id, payload, only_ids)
    if failed and self.request.retries < len(PUSH_RETRY_DELAYS):
        raise self.retry(args=[user_id, payload, failed], countdown=PUSH_RETRY_DELAYS[self.request.retries])

from django.conf import settings

from .models import Report


def app_flags(request):
    """Small values every template can use."""
    return {
        "report_reasons": Report.REASON_CHOICES,
        "email_enabled": settings.EMAIL_ENABLED,  # False until Resend + musicluvers.site are verified
        "push_enabled": settings.PUSH_ENABLED,
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
        "posthog_key": settings.POSTHOG_KEY,  # empty outside production -> no analytics script
        "posthog_host": settings.POSTHOG_HOST,
    }

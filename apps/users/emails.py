"""Outgoing email. Sent from the Celery worker so a slow mail server never slows a page down."""
from django.conf import settings
from django.template.loader import render_to_string


def send_email(to, subject, text, html=None):
    from .tasks import send_email_task
    send_email_task.delay(to, subject, text, html)


def send_templated(to, subject, template, context):
    """Renders users/emails/<template>.txt and .html (the HTML one is optional)."""
    context = {"site_name": "Music Luvers", **context}
    text = render_to_string(f"users/emails/{template}.txt", context)
    try:
        html = render_to_string(f"users/emails/{template}.html", context)
    except Exception:
        html = None
    send_email(to, subject, text, html)


def deliver(to, subject, text, html=None):
    """Actually sends (called by the worker)."""
    from django.core.mail import EmailMultiAlternatives
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    if html:
        message.attach_alternative(html, "text/html")
    message.send()

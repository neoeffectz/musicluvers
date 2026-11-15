"""Email verification, password reset email, and account deletion."""
import logging

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.core import signing
from django.urls import reverse

from . import emails
from .models import CustomUser

logger = logging.getLogger(__name__)
VERIFY_SALT = "users.verify-email"


# --- email verification -------------------------------------------------------

def verify_token(user):
    # The email is inside the token, so changing your email makes old links stop working.
    return signing.dumps({"u": str(user.id), "e": user.email}, salt=VERIFY_SALT)


def send_verification(request, user):
    if not settings.EMAIL_ENABLED or user.email_verified or not user.email:
        return False
    link = request.build_absolute_uri(reverse("users:verify_email", args=[verify_token(user)]))
    emails.send_templated(user.email, "Confirm your email for Music Luvers", "verify_email",
                          {"user": user, "link": link})
    return True


def confirm_token(token):
    """Returns the user the link was for (now verified), or None if it's wrong/expired."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFY_MAX_AGE)
    except signing.BadSignature:
        return None
    user = CustomUser.objects.filter(id=data.get("u"), email=data.get("e")).first()
    if user and not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
    return user


# --- password reset -------------------------------------------------------------

class QueuedPasswordResetForm(PasswordResetForm):
    """Django's reset form, but the email goes out from the worker (never slows the page),
    and it never says whether an email has an account (stops people fishing for users)."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from django.template import loader
        subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
        text = loader.render_to_string(email_template_name, context)
        html = loader.render_to_string(html_email_template_name, context) if html_email_template_name else None
        emails.send_email(to_email, subject, text, html)


# --- delete account ---------------------------------------------------------------

def delete_account(user):
    """Deletes the account and everything attached: photos (also from Cloudinary), music, matches,
    chats, notifications, location. Reports *about* other people are kept but no longer name the reporter."""
    import cloudinary.uploader
    for photo in user.photos.all():
        try:
            cloudinary.uploader.destroy(str(photo.image.public_id))
        except Exception:
            logger.exception("Couldn't delete photo %s from Cloudinary", photo.id)
    user.delete()

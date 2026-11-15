from django.conf import settings
from django.db import models
from .fields import EncryptedTextField
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
import uuid
from datetime import date

class CustomUser(AbstractUser):
    GENDER_CHOICES = [("man", "Man"), ("woman", "Woman"), ("nonbinary", "Non-binary")]
    PREFERENCE_CHOICES = [("man", "Men"), ("woman", "Women"), ("everyone", "Everyone")]
    DISTANCE_CHOICES = [(25, "25 km"), (50, "50 km"), (100, "100 km"), (0, "Anywhere")]
    DEFAULT_DISTANCE_KM = 50

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spotify_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    spotify_access_token = EncryptedTextField(null=True, blank=True)
    spotify_refresh_token = EncryptedTextField(null=True, blank=True)
    spotify_token_expires_at = models.DateTimeField(null=True, blank=True)
    has_seen_taste_intro = models.BooleanField(default=False)
    spotify_profile_image_url = models.URLField(blank=True, null=True)
    spotify_country = models.CharField(max_length=2, blank=True, null=True)
    bio = models.TextField(blank=True, default="")
    birthdate = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True, default="")
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True, default="")
    preference = models.CharField(max_length=20, choices=PREFERENCE_CHOICES, default="everyone")
    lastfm_username = models.CharField(max_length=64, blank=True, default="")
    taste_synced_at = models.DateTimeField(null=True, blank=True)
    favorite_genres = models.JSONField(default=list, blank=True)
    # Rough position (rounded to ~1 km, never the exact spot) for distance-based Discover.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    max_distance_km = models.PositiveSmallIntegerField(choices=DISTANCE_CHOICES, default=DEFAULT_DISTANCE_KM)
    email_verified = models.BooleanField(default=False)

    @property
    def has_position(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def finished_about_you(self):
        """Location, gender and birthday are required before using the app (the "About you" step)."""
        return self.has_position and bool(self.gender) and self.birthdate is not None

    def has_connected_taste_source(self):
        return bool(self.lastfm_username)

    @property
    def photo_url(self):
        """Main photo. Looked up once per object (and free when a list was loaded with
        prefetch_related("photos")) — pages show the same person's photo several times."""
        if "_photo_url" not in self.__dict__:
            prefetched = getattr(self, "_prefetched_objects_cache", {}).get("photos")
            photo = (prefetched[0] if prefetched else None) if prefetched is not None else self.photos.first()
            self.__dict__["_photo_url"] = photo.image.url if photo else self.spotify_profile_image_url
        return self.__dict__["_photo_url"]

    def forget_photo(self):
        """Call after adding/removing photos so the next photo_url is fresh."""
        self.__dict__.pop("_photo_url", None)
        getattr(self, "_prefetched_objects_cache", {}).pop("photos", None)

    @property
    def age(self):
        if not self.birthdate:
            return None
        today = date.today()
        return today.year - self.birthdate.year - ((today.month, today.day) < (self.birthdate.month, self.birthdate.day))


class ProfilePhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="photos")
    image = CloudinaryField("image")
    rank = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["rank", "created_at"]

class Block(models.Model):
    """`blocker` never sees `blocked` again, and vice versa (Discover, matches, chat)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["blocker", "blocked"]


class Report(models.Model):
    REASON_CHOICES = [
        ("fake", "Fake profile or catfish"),
        ("photos", "Inappropriate photos"),
        ("harassment", "Harassment or abusive messages"),
        ("scam", "Scam, spam or asking for money"),
        ("underage", "Looks under 18"),
        ("other", "Something else"),
    ]
    STATUS_CHOICES = [("open", "Open"), ("action_taken", "Action taken"), ("dismissed", "Dismissed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="reports_made")
    reported = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_received")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    details = models.TextField(blank=True, default="", max_length=1000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class PushSubscription(models.Model):
    """One per phone/browser that said yes to notifications."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

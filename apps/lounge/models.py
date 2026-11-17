from django.conf import settings
from django.db import models


class Round(models.Model):
    """One round of a Friday Lounge: requests are collected, then up to 10 are drawn and played in order."""
    session_date = models.DateField(db_index=True)       # the Friday this round belongs to
    number = models.PositiveSmallIntegerField()
    opened_at = models.DateTimeField()                   # requests open from here
    started_at = models.DateTimeField(null=True, blank=True)  # set when the playlist is drawn and starts
    is_warmup = models.BooleanField(default=False)  # music for the first 10 minutes, before the first draw

    class Meta:
        unique_together = ["session_date", "number"]
        ordering = ["session_date", "number"]

    def __str__(self):
        return f"Lounge {self.session_date} round {self.number}"


class SongRequest(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="requests")
    # Empty only for warm-up starter songs nobody picked (the first Friday ever).
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lounge_requests",
                             null=True, blank=True)
    track_id = models.CharField(max_length=64)
    track_name = models.CharField(max_length=255)
    artist_name = models.CharField(max_length=255, blank=True)
    image_url = models.URLField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(default=0)
    youtube_id = models.CharField(max_length=32, blank=True, default="")  # full song for the shared player
    position = models.PositiveSmallIntegerField(null=True, blank=True)  # play order once drawn
    from_prompt = models.CharField(max_length=80, blank=True, default="")  # warm-up: "The anthem of my life"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One pending request per person per round (drawn and warm-up songs can repeat a person).
        constraints = [models.UniqueConstraint(fields=["round", "user"], condition=models.Q(position__isnull=True),
                                               name="lounge_one_request_per_round")]
        ordering = ["position", "created_at"]


class Presence(models.Model):
    """Who is in the Lounge right now (refreshed by the open page every ~45 s)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True,
                                related_name="lounge_presence")
    last_seen = models.DateTimeField()


class TrackVideo(models.Model):
    """Which YouTube upload plays a song. Looked up once, kept for good: YouTube's daily limit only pays for
    songs the Lounge has never played before. youtube_id is empty when no playable upload was found."""
    track_id = models.CharField(max_length=64, primary_key=True)   # Spotify track id
    youtube_id = models.CharField(max_length=32, blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    looked_up_at = models.DateTimeField(auto_now=True)

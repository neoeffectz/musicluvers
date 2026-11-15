import uuid
from django.conf import settings
from django.db import models


SOURCE_MANUAL = "manual"
SOURCE_SPOTIFY = "spotify"
SOURCE_LASTFM = "lastfm"
SOURCE_CHOICES = [(SOURCE_MANUAL, "Picked by hand"), (SOURCE_SPOTIFY, "Spotify"), (SOURCE_LASTFM, "Last.fm")]


class TopArtist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="top_artists")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_SPOTIFY)
    external_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    genres = models.JSONField(default=list)
    image_url = models.URLField(blank=True, null=True)
    rank = models.PositiveSmallIntegerField()
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank"]
        unique_together = ["user", "source", "external_id"]


class TopTrack(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="top_tracks")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_SPOTIFY)
    external_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    artist_name = models.CharField(max_length=255)
    rank = models.PositiveSmallIntegerField()
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank"]
        unique_together = ["user", "source", "external_id"]


class MatchScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    raw_score = models.FloatField()
    display_score = models.PositiveSmallIntegerField()
    shared_answers = models.JSONField(default=list)  # "You both picked 'Essence'" etc., from profile questions
    shared_artists = models.JSONField(default=list)
    shared_genres = models.JSONField(default=list)
    shared_tracks = models.JSONField(default=list)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user_a", "user_b"]


class Swipe(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="swipes_made", on_delete=models.CASCADE)
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="swipes_received", on_delete=models.CASCADE)
    liked = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["from_user", "to_user"]


class Match(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user_a", "user_b"]


class Notification(models.Model):
    KIND_CHOICES = [("match_created", "Match created"), ("new_message", "New message")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ProfilePrompt(models.Model):
    PROMPT_CHOICES = [
        ("dance", "A song that makes me dance"),
        ("underrated", "An underrated song people should know"),
        ("anthem", "The anthem of my life"),
        # Extra questions, answered from My Sound (not asked during signup)
        ("rising_artist", "A rising artist the world should know"),
        ("album", "A 10/10 album"),
        ("wedding", "A song that must be played at my wedding"),
        ("warm_hug", "A song that feels like a warm hug"),
    ]
    SIGNUP_PROMPTS = ("dance", "underrated", "anthem")
    # Everything else is answered with a song.
    PROMPT_KINDS = {"rising_artist": "artist", "album": "album"}

    @property
    def kind(self):
        return self.PROMPT_KINDS.get(self.prompt, "song")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prompts")
    prompt = models.CharField(max_length=32, choices=PROMPT_CHOICES)
    track_id = models.CharField(max_length=64)
    track_name = models.CharField(max_length=255)
    artist_name = models.CharField(max_length=255)
    image_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = ["user", "prompt"]

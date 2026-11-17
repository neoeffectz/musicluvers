import uuid
from django.conf import settings
from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_a = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    user_b = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user_a", "user_b"]

    


class ConcertPlan(models.Model):
    """A concert two matches plan to go to together, kept in their chat ("Our Concerts")."""
    PROPOSED, AGREED, DECLINED = "proposed", "agreed", "declined"
    STATUS_CHOICES = [(PROPOSED, "Waiting for an answer"), (AGREED, "Agreed"), (DECLINED, "Not this one")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, related_name="concerts", on_delete=models.CASCADE)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    title = models.CharField(max_length=120)          # artist or event
    venue = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    ticket_url = models.URLField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PROPOSED)
    tickets = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+", blank=True)   # who has theirs
    loved_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="+", blank=True)  # after the date
    reminded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self):
        return f"{self.title} at {self.venue}"


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, related_name="messages", on_delete=models.CASCADE)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    body = models.TextField()
    liked_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="liked_messages", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # Set on the chat card that proposes a concert (shows Agree / Not this one to the other person).
    concert = models.ForeignKey(ConcertPlan, null=True, blank=True, related_name="+", on_delete=models.SET_NULL)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]
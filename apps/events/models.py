import uuid
from django.db import models


class OutboxEvent(models.Model):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (DONE, "Done"), (FAILED, "Failed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "updated_at"])]

    def __str__(self):
        return f"{self.name} ({self.status})"


class ProcessedEvent(models.Model):
    event = models.ForeignKey(OutboxEvent, on_delete=models.CASCADE, related_name="processed_by")
    handler = models.CharField(max_length=255)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["event", "handler"]

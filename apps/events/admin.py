from django.contrib import admin, messages

from .models import OutboxEvent, ProcessedEvent
from .tasks import dispatch_event_task


class ProcessedEventInline(admin.TabularInline):
    model = ProcessedEvent
    extra = 0
    readonly_fields = ("handler", "processed_at")
    can_delete = False


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "attempts", "created_at", "processed_at")
    list_filter = ("status", "name")
    readonly_fields = ("id", "name", "payload", "attempts", "last_error", "created_at", "updated_at", "processed_at")
    inlines = [ProcessedEventInline]
    actions = ["replay"]

    @admin.action(description="Replay selected events")
    def replay(self, request, queryset):
        count = 0
        for event in queryset.exclude(status=OutboxEvent.DONE):
            event.status = OutboxEvent.PENDING
            event.attempts = 0
            event.save(update_fields=["status", "attempts", "updated_at"])
            dispatch_event_task.delay(str(event.id))
            count += 1
        self.message_user(request, f"Replayed {count} event(s).", messages.SUCCESS)

# apps/users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Block, CustomUser, ProfilePhoto, Report


class CustomUserAdmin(UserAdmin):
    list_display = ("username", "first_name", "email", "location", "email_verified", "is_active", "is_staff")
    list_filter = ("is_active", "email_verified", "is_staff")
    search_fields = ("username", "first_name", "email")
    fieldsets = UserAdmin.fieldsets + (
        ("Music", {"fields": ("spotify_id", "spotify_profile_image_url", "lastfm_username", "taste_synced_at", "has_seen_taste_intro")}),
        ("Profile", {"fields": ("bio", "birthdate", "location", "latitude", "longitude", "max_distance_km", "gender", "preference", "email_verified")}),
    )


@admin.register(ProfilePhoto)
class ProfilePhotoAdmin(admin.ModelAdmin):
    list_display = ("user", "rank", "created_at")


admin.site.register(CustomUser, CustomUserAdmin)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Moderation queue. "Ban" turns the account off (can't log in, hidden everywhere); it can be undone."""
    list_display = ("created_at", "reported", "reason", "status", "reporter", "reports_against")
    list_filter = ("status", "reason")
    search_fields = ("reported__email", "reported__first_name", "details")
    readonly_fields = ("reporter", "reported", "reason", "details", "created_at")
    actions = ["ban_reported_users", "mark_dismissed"]

    @admin.display(description="Reports against them")
    def reports_against(self, obj):
        return Report.objects.filter(reported=obj.reported).count()

    @admin.action(description="Ban the reported account(s) and close these reports")
    def ban_reported_users(self, request, queryset):
        users = CustomUser.objects.filter(id__in=queryset.values("reported_id"))
        banned = users.update(is_active=False)
        queryset.update(status="action_taken")
        self.message_user(request, f"Banned {banned} account(s).")

    @admin.action(description="Dismiss (no action needed)")
    def mark_dismissed(self, request, queryset):
        queryset.update(status="dismissed")


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("created_at", "blocker", "blocked")
    search_fields = ("blocker__email", "blocked__email")

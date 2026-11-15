from django.contrib import admin
from .models import TopArtist, TopTrack, MatchScore, Swipe, Match, Notification


@admin.register(TopArtist)
class TopArtistAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "source", "rank", "external_id", "synced_at")
    list_filter = ("source", "synced_at")
    search_fields = ("name", "user__username", "user__email", "external_id")
    readonly_fields = ("id", "synced_at")
    ordering = ("user", "rank")


@admin.register(TopTrack)
class TopTrackAdmin(admin.ModelAdmin):
    list_display = ("name", "artist_name", "user", "source", "rank", "external_id", "synced_at")
    list_filter = ("source", "synced_at")
    search_fields = ("name", "artist_name", "user__username", "user__email", "external_id")
    readonly_fields = ("id", "synced_at")
    ordering = ("user", "rank")


@admin.register(MatchScore)
class MatchScoreAdmin(admin.ModelAdmin):
    list_display = ("user_a", "user_b", "display_score", "raw_score", "computed_at")
    list_filter = ("computed_at",)
    search_fields = ("user_a__username", "user_a__email", "user_b__username", "user_b__email")
    readonly_fields = ("id", "computed_at")


@admin.register(Swipe)
class SwipeAdmin(admin.ModelAdmin):
    list_display = ("from_user", "to_user", "liked", "created_at")
    list_filter = ("liked", "created_at")
    search_fields = (
        "from_user__username",
        "from_user__email",
        "to_user__username",
        "to_user__email",
    )
    readonly_fields = ("id", "created_at")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("user_a", "user_b", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user_a__username", "user_a__email", "user_b__username", "user_b__email")
    readonly_fields = ("id", "created_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "created_at", "read_at")
    list_filter = ("kind", "created_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("id", "created_at")
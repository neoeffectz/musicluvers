from django.urls import path
from . import views

app_name = "spotify_match"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/tab/sound/", views.my_sound_tab, name="tab_sound"),
    path("dashboard/tab/discover/", views.discover_tab, name="tab_discover"),
    path("notifications/", views.notifications_panel, name="notifications"),
    path("notifications/badge/", views.notifications_badge, name="notifications_badge"),
    path("dashboard/swipe/", views.swipe_partial, name="swipe"),
    path("user/<uuid:user_id>/", views.user_detail, name="user_detail"),
    path("matches/", views.all_matches, name="all_matches"),
    path("user/<uuid:user_id>/unmatch/", views.unmatch_view, name="unmatch"),
    path("taste/build/", views.build_taste, name="build_taste"),
    path("taste/genres/", views.taste_genres, name="taste_genres"),
    path("taste/suggestions/", views.taste_suggestions, name="taste_suggestions"),
    path("taste/songs/", views.taste_songs, name="taste_songs"),
    path("taste/songs/search/", views.song_search, name="song_search"),
    path("taste/songs/set/", views.song_set, name="song_set"),
    path("taste/songs/clear/<str:prompt>/", views.song_clear, name="song_clear"),
    path("taste/songs/done/", views.songs_done, name="songs_done"),
    path("dashboard/artists/", views.artist_deck, name="artist_deck"),
    path("dashboard/artists/swipe/", views.artist_swipe, name="artist_swipe"),
    path("taste/search/", views.taste_search, name="taste_search"),
    path("taste/add/", views.taste_add, name="taste_add"),
    path("taste/remove/<uuid:artist_id>/", views.taste_remove, name="taste_remove"),
    path("taste/finish/", views.taste_finish, name="taste_finish"),
    path("taste/lastfm/", views.connect_lastfm, name="connect_lastfm"),
    path("taste/resync/", views.resync_taste, name="resync_taste"),
]
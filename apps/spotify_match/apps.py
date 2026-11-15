from django.apps import AppConfig


class SpotifyMatchConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.spotify_match'

    def ready(self):
        from . import handlers  # noqa: F401  registers event subscribers

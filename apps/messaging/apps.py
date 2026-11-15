from django.apps import AppConfig


class MessagingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.messaging'

    def ready(self):
        from . import handlers  # noqa: F401  registers event subscribers

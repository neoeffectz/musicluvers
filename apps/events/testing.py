from config.celery import celery_app

from .models import OutboxEvent


class EagerEventsMixin:
    """Run Celery tasks inline so a test can follow an event end to end.

    Wrap the action in `with self.run_events():` so on-commit hooks fire
    (TestCase never really commits) and any follow-up events cascade too.
    """

    def setUp(self):
        super().setUp()
        previous = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        self.addCleanup(setattr, celery_app.conf, "task_always_eager", previous)

    def run_events(self):
        return self.captureOnCommitCallbacks(execute=True)

    def published(self, name):
        return list(OutboxEvent.objects.filter(name=name).order_by("created_at"))

from datetime import timedelta
from unittest import mock

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from . import bus
from .models import OutboxEvent, ProcessedEvent
from .tasks import sweep_outbox_task
from .testing import EagerEventsMixin

TEST_EVENT = "test.something_happened"


class BusTestCase(TestCase):
    def setUp(self):
        super().setUp()
        saved = list(bus._handlers[TEST_EVENT])
        self.addCleanup(bus._handlers.__setitem__, TEST_EVENT, saved)
        bus._handlers[TEST_EVENT] = []
        self.calls = []

    def subscribe(self, fail_times=0, label="handler"):
        state = {"failures_left": fail_times}

        def handler(payload):
            if state["failures_left"]:
                state["failures_left"] -= 1
                raise RuntimeError("boom")
            self.calls.append((label, payload))

        handler.__qualname__ = f"test_{label}"
        bus._handlers[TEST_EVENT].append(handler)
        return handler


class PublishTests(BusTestCase):
    def test_writes_pending_outbox_row_and_waits_for_commit(self):
        with mock.patch("apps.events.tasks.dispatch_event_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                event = bus.publish(TEST_EVENT, n=1)
            delay.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            delay.assert_called_once_with(str(event.id))

        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.PENDING)
        self.assertEqual(event.payload, {"n": 1})

    def test_rolled_back_transaction_leaves_no_event(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                bus.publish(TEST_EVENT)
                raise RuntimeError("the state change failed")
        self.assertFalse(OutboxEvent.objects.filter(name=TEST_EVENT).exists())

    def test_broker_outage_does_not_break_the_request(self):
        with mock.patch("apps.events.tasks.dispatch_event_task.delay", side_effect=ConnectionError):
            with self.captureOnCommitCallbacks(execute=True):
                event = bus.publish(TEST_EVENT)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.PENDING)


class DispatchTests(BusTestCase):
    def test_runs_handlers_and_marks_done(self):
        self.subscribe()
        event = bus.publish(TEST_EVENT, n=1)

        self.assertTrue(bus.dispatch(event.id))

        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.DONE)
        self.assertEqual(self.calls, [("handler", {"n": 1})])

    def test_redelivery_is_a_no_op(self):
        self.subscribe()
        event = bus.publish(TEST_EVENT)
        bus.dispatch(event.id)
        bus.dispatch(event.id)
        self.assertEqual(len(self.calls), 1)

    def test_retry_skips_handlers_that_already_succeeded(self):
        self.subscribe(label="ok")
        self.subscribe(fail_times=1, label="flaky")
        event = bus.publish(TEST_EVENT)

        self.assertFalse(bus.dispatch(event.id))
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.PENDING)
        self.assertEqual(event.attempts, 1)
        self.assertIn("boom", event.last_error)

        self.assertTrue(bus.dispatch(event.id))
        self.assertEqual([label for label, _ in self.calls], ["ok", "flaky"])
        self.assertEqual(ProcessedEvent.objects.filter(event=event).count(), 2)

    def test_gives_up_after_max_attempts(self):
        self.subscribe(fail_times=99)
        event = bus.publish(TEST_EVENT)
        for _ in range(bus.MAX_ATTEMPTS):
            bus.dispatch(event.id)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.FAILED)
        self.assertEqual(event.attempts, bus.MAX_ATTEMPTS)


class EagerDeliveryTests(EagerEventsMixin, BusTestCase):
    def test_event_is_delivered_after_commit(self):
        self.subscribe()
        with self.run_events():
            bus.publish(TEST_EVENT, n=2)
        self.assertEqual(self.calls, [("handler", {"n": 2})])


class SweeperTests(TestCase):
    def test_resends_only_stale_pending_events(self):
        stale = OutboxEvent.objects.create(name=TEST_EVENT)
        fresh = OutboxEvent.objects.create(name=TEST_EVENT)
        done = OutboxEvent.objects.create(name=TEST_EVENT, status=OutboxEvent.DONE)
        old = timezone.now() - timedelta(minutes=5)
        OutboxEvent.objects.filter(id__in=[stale.id, done.id]).update(updated_at=old)

        with mock.patch("apps.events.tasks.dispatch_event_task.delay") as delay:
            sweep_outbox_task()

        delay.assert_called_once_with(str(stale.id))

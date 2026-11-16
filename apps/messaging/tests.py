from unittest import mock
from django.test import TestCase
from apps.events import catalog
from apps.events.testing import EagerEventsMixin
from apps.users.models import CustomUser
from apps.spotify_match.models import Match
from . import services
from .models import Conversation, Message


class AppLoadsTests(TestCase):
    def test_full_asgi_app_with_websocket_routes_imports(self):
        """The live server loads chat/websocket code that normal page tests never import."""
        import importlib
        import apps.events.consumers
        import apps.messaging.consumers
        import config.asgi
        for module in (apps.messaging.consumers, apps.events.consumers, config.asgi):
            importlib.reload(module)


def make_user(username):
    from datetime import date
    return CustomUser.objects.create_user(username=username, password="pass1234", latitude=6.52, longitude=3.38,
                                          gender="woman", birthdate=date(1998, 5, 1))


class IsMatchedTests(TestCase):
    def test_true_when_matched(self):
        a = make_user("alice")
        b = make_user("bob")
        user_a, user_b = sorted([a, b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)
        self.assertTrue(services._is_matched(a, b))
        self.assertTrue(services._is_matched(b, a))

    def test_false_when_not_matched(self):
        a = make_user("alice")
        b = make_user("bob")
        self.assertFalse(services._is_matched(a, b))


class GetOrCreateConversationTests(TestCase):
    def test_raises_when_not_matched(self):
        a = make_user("alice")
        b = make_user("bob")
        with self.assertRaises(PermissionError):
            services.get_or_create_conversation(a, b)

    def test_same_conversation_regardless_of_argument_order(self):
        a = make_user("alice")
        b = make_user("bob")
        user_a, user_b = sorted([a, b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)

        conv1 = services.get_or_create_conversation(a, b)
        conv2 = services.get_or_create_conversation(b, a)

        self.assertEqual(conv1.id, conv2.id)
        self.assertEqual(len(services.get_conversations_for(a)), 1)


class SendMessageTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.outsider = make_user("carol")
        user_a, user_b = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)
        self.conversation = services.get_or_create_conversation(self.a, self.b)

    def test_raises_for_non_participant(self):
        with self.assertRaises(PermissionError):
            services.send_message(self.conversation, self.outsider, "hi")

    def test_succeeds_for_participant(self):
        message = services.send_message(self.conversation, self.a, "hi")
        self.assertEqual(message.body, "hi")
        self.assertEqual(message.sender, self.a)


class GetMessagesTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.outsider = make_user("carol")
        user_a, user_b = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)
        self.conversation = services.get_or_create_conversation(self.a, self.b)

    def test_raises_for_non_participant(self):
        with self.assertRaises(PermissionError):
            services.get_messages(self.conversation, self.outsider)

    def test_pagination_across_page_boundary(self):
        for i in range(services.PAGE_SIZE + 5):
            services.send_message(self.conversation, self.a, f"message {i}")

        page1, has_more = services.get_messages(self.conversation, self.a)
        self.assertEqual(len(page1), services.PAGE_SIZE)
        self.assertTrue(has_more)

        oldest_on_page1 = page1[0]
        page2, has_more2 = services.get_messages(self.conversation, self.a, before=oldest_on_page1.id)
        self.assertEqual(len(page2), 5)
        self.assertFalse(has_more2)

        page1_ids = {m.id for m in page1}
        page2_ids = {m.id for m in page2}
        self.assertEqual(page1_ids & page2_ids, set())


class ToggleLikeTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.outsider = make_user("carol")
        user_a, user_b = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)
        self.conversation = services.get_or_create_conversation(self.a, self.b)
        self.message = services.send_message(self.conversation, self.a, "hi")

    def test_raises_for_non_participant(self):
        with self.assertRaises(PermissionError):
            services.toggle_like(self.message, self.outsider)

    def test_toggles_on_and_off(self):
        liked = services.toggle_like(self.message, self.b)
        self.assertTrue(liked)
        liked_again = services.toggle_like(self.message, self.b)
        self.assertFalse(liked_again)


class MessageSentEventTests(EagerEventsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.a = make_user("alice")
        self.b = make_user("bob")
        user_a, user_b = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=user_a, user_b=user_b)
        self.conversation = services.get_or_create_conversation(self.a, self.b)

    @mock.patch("apps.messaging.handlers.get_channel_layer")
    def test_sending_broadcasts_through_the_event_bus(self, get_layer):
        layer = get_layer.return_value
        layer.group_send = mock.AsyncMock()

        with self.run_events():
            message = services.send_message(self.conversation, self.a, "hi")

        self.assertEqual(len(self.published(catalog.MESSAGE_SENT)), 1)
        group, payload = layer.group_send.call_args.args
        self.assertEqual(group, f"conversation_{self.conversation.id}")
        self.assertEqual(payload["message"]["id"], str(message.id))



class SendRulesTests(TestCase):
    """Every send path (page + API) refuses blocked/unmatched people and over-long messages."""
    def setUp(self):
        from apps.spotify_match.models import Match
        from apps.users.models import CustomUser
        self.a = CustomUser.objects.create_user(username="sa", password="x")
        self.b = CustomUser.objects.create_user(username="sb", password="x")
        x, y = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=x, user_b=y)
        self.conv = Conversation.objects.create(user_a=x, user_b=y)

    @mock.patch("apps.messaging.services.bus.publish")
    def test_too_long_and_empty(self, _):
        with self.assertRaises(ValueError):
            services.send_message(self.conv, self.a, "x" * 2001)
        with self.assertRaises(ValueError):
            services.send_message(self.conv, self.a, "   ")
        services.send_message(self.conv, self.a, "x" * 2000)

    @mock.patch("apps.messaging.services.bus.publish")
    def test_blocked_cannot_message_even_before_chat_is_deleted(self, _):
        from apps.users.models import Block
        Block.objects.create(blocker=self.b, blocked=self.a)
        with self.assertRaises(PermissionError):
            services.send_message(self.conv, self.a, "still here?")
        self.client.force_login(self.a)
        import json
        api = self.client.post(f"/api/messages/conversations/{self.conv.id}/messages",
                               json.dumps({"body": "via api"}), content_type="application/json")
        self.assertEqual(api.status_code, 403)
        self.assertFalse(Message.objects.filter(body__in=["still here?", "via api"]).exists())


class InstantDeliveryTests(TestCase):
    """A sent message reaches the open chat straight from the request, not after the background worker."""
    def setUp(self):
        self.a, self.b = make_user("ia"), make_user("ib")
        x, y = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=x, user_b=y)
        self.conversation = Conversation.objects.create(user_a=x, user_b=y)

    @mock.patch("channels.layers.get_channel_layer")
    def test_sent_message_is_pushed_live_right_away(self, layer):
        layer.return_value.group_send = mock.AsyncMock()
        with self.captureOnCommitCallbacks(execute=True):
            message = services.send_message(self.conversation, self.a, "hey")
        group, event = layer.return_value.group_send.call_args.args
        self.assertEqual(group, f"conversation_{self.conversation.id}")
        self.assertEqual(event["message"]["id"], str(message.id))
        self.assertEqual(event["message"]["body"], "hey")

    @mock.patch("channels.layers.get_channel_layer", side_effect=RuntimeError("redis down"))
    def test_live_push_failing_doesnt_lose_the_message(self, _):
        with self.captureOnCommitCallbacks(execute=True):
            services.send_message(self.conversation, self.a, "still saved")
        self.assertTrue(Message.objects.filter(body="still saved").exists())


class InboxUpdatesInstantlyTests(TestCase):
    def setUp(self):
        self.a, self.b = make_user("ua"), make_user("ub")
        x, y = sorted([self.a, self.b], key=lambda u: str(u.id))
        Match.objects.create(user_a=x, user_b=y)
        self.conversation = Conversation.objects.create(user_a=x, user_b=y)

    @mock.patch("apps.events.realtime.get_channel_layer")
    @mock.patch("channels.layers.get_channel_layer")
    def test_recipient_is_told_right_away(self, chat_layer, user_layer):
        chat_layer.return_value.group_send = mock.AsyncMock()
        user_layer.return_value.group_send = mock.AsyncMock()
        with self.captureOnCommitCallbacks(execute=True):
            services.send_message(self.conversation, self.a, "hi")
        group, event = user_layer.return_value.group_send.call_args.args
        self.assertEqual(group, f"user_{self.b.id}")
        self.assertEqual(event["event"], {"kind": "new_message", "conversation_id": str(self.conversation.id)})

    def test_messages_tab_shows_a_dot_for_unread(self):
        Message.objects.create(conversation=self.conversation, sender=self.a, body="unread one")
        self.client.force_login(self.b)
        page = self.client.get("/matches/").content.decode()
        self.assertIn('has-new" data-tab="messages"', page)
        self.client.force_login(self.a)  # the sender has nothing unread
        self.assertNotIn('has-new" data-tab="messages"', self.client.get("/matches/").content.decode())

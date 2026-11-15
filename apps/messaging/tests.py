from unittest import mock
from django.test import TestCase
from apps.events import catalog
from apps.events.testing import EagerEventsMixin
from apps.users.models import CustomUser
from apps.spotify_match.models import Match
from . import services
from .models import Conversation, Message


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

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db import models

from .models import Conversation

logger = logging.getLogger(__name__)


class ThreadConsumer(AsyncWebsocketConsumer):
    """Live chat: everyone with this conversation open gets new messages as they're sent."""

    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"conversation_{self.conversation_id}"
        user = self.scope["user"]

        if not user.is_authenticated:
            logger.info("Chat socket refused: not logged in")
            await self.close()
            return

        if not await self.user_is_participant(user):
            logger.info("Chat socket refused: not part of conversation %s", self.conversation_id)
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    @database_sync_to_async
    def user_is_participant(self, user):
        return Conversation.objects.filter(
            models.Q(user_a=user) | models.Q(user_b=user),
            id=self.conversation_id,
        ).exists()

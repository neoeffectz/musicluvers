import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db import models
from .models import Conversation

import logging
logger = logging.getLogger(__name__)


class ThreadConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = f"conversation_{self.conversation_id}"
        user = self.scope["user"]

        logger.warning(f"WS connect attempt: user={user}, authenticated={user.is_authenticated}, conv_id={self.conversation_id}")

        if not user.is_authenticated:
            logger.warning("WS closing: user not authenticated")
            await self.close()
            return

        allowed = await self.user_is_participant(user)
        logger.warning(f"WS participant check: allowed={allowed}")
        if not allowed:
            logger.warning("WS closing: not a participant")
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.warning("WS accepted")

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    
    async def chat_message(self, event):
        logger.info(f"Consumer for {self.conversation_id} received event, forwarding to client")
        await self.send(text_data=json.dumps(event["message"]))

    @database_sync_to_async
    def user_is_participant(self, user):
        return Conversation.objects.filter(
            models.Q(user_a=user) | models.Q(user_b=user),
            id=self.conversation_id,
        ).exists()
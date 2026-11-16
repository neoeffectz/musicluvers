import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from . import schedule, services


class LoungeConsumer(AsyncWebsocketConsumer):
    """Keeps an open Lounge page live: people count, request count, and "the playlist changed"."""

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated or not schedule.is_open():
            await self.close()
            return
        await self.channel_layer.group_add(services.GROUP, self.channel_name)
        await self.accept()
        await self._check_in()
        await self._announce_count()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(services.GROUP, self.channel_name)
        user = self.scope["user"]
        if user.is_authenticated:
            await database_sync_to_async(services.check_out)(user)
            await self._announce_count()

    async def receive(self, text_data=None, bytes_data=None):
        await self._check_in()  # the page pings every ~45 s while it's open

    async def lounge_event(self, event):
        await self.send(text_data=json.dumps(event["event"]))

    @database_sync_to_async
    def _check_in(self):
        services.check_in(self.scope["user"])

    async def _announce_count(self):
        n = await database_sync_to_async(services.people_here)()
        await self.channel_layer.group_send(services.GROUP, {"type": "lounge_event", "event": {"kind": "people", "n": n}})

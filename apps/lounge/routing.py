from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/lounge/$", consumers.LoungeConsumer.as_asgi()),
]

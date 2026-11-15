from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def user_group(user_id):
    return f"user_{user_id}"


def push_to_user(user_id, kind, **data):
    async_to_sync(get_channel_layer().group_send)(
        user_group(user_id),
        {"type": "user_event", "event": {"kind": kind, **data}},
    )

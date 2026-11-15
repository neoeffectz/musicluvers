from ninja import Schema
from typing import Optional
from datetime import datetime


class OtherUserOut(Schema):
    user_id: str
    display_name: str
    profile_image_url: Optional[str]


class ConversationOut(Schema):
    conversation_id: str
    other_user: OtherUserOut
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int


class MessageOut(Schema):
    message_id: str
    sender_id: str
    body: str
    created_at: datetime
    is_mine: bool


class SendMessageIn(Schema):
    body: str


class OpenConversationIn(Schema):
    other_user_id: str
from ninja import Schema
from typing import Optional


class UserOut(Schema):
    username: str
    first_name: str
    email: str
    spotify_id: Optional[str]
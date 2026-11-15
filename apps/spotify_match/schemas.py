from ninja import Schema
from typing import Optional


class GenreBreakdownOut(Schema):
    genre: str
    pct: int


class ArtistOut(Schema):
    name: str
    image_url: Optional[str]
    rank: int


class TrackOut(Schema):
    name: str
    artist_name: str
    rank: int


class TasteProfileOut(Schema):
    display_name: str
    profile_image_url: Optional[str]
    top_genre: Optional[str]
    top_genre_pct: int
    genre_breakdown: list[GenreBreakdownOut]
    top_artists: list[ArtistOut]
    top_tracks: list[TrackOut]

class DiscoveryCardOut(Schema):
    user_id: str
    display_name: str
    country: Optional[str]
    profile_image_url: Optional[str]
    score: int
    shared_artists: list[str]
    shared_tracks: list[str]


class SyncStartedOut(Schema):
    status: str


class SwipeIn(Schema):
    to_user_id: str
    liked: bool


class SwipeOut(Schema):
    matched: bool
    matched_user_name: Optional[str] = None


class MatchOut(Schema):
    user_id: str
    display_name: str
    country: Optional[str]
    profile_image_url: Optional[str]

class UserProfileOut(Schema):
    user_id: str
    display_name: str
    profile_image_url: Optional[str]
    country: Optional[str]
    score: Optional[int]
    shared_artists: list[str]
    shared_tracks: list[str]
    top_artists: list[ArtistOut]
    top_tracks: list[TrackOut]
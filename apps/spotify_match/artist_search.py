"""Spotify catalog lookups using the app's own (client-credentials) token.

No user login is involved, so this is not limited by the development-mode
user allowlist.
"""
import requests
from django.core.cache import cache

from apps.users.services import TOKEN_URL, _basic_auth_header

SEARCH_URL = "https://api.spotify.com/v1/search"
ARTIST_URL = "https://api.spotify.com/v1/artists/{id}"
TOKEN_CACHE_KEY = "spotify_app_token"


def _app_token():
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers=_basic_auth_header(),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    cache.set(TOKEN_CACHE_KEY, data["access_token"], timeout=max(data.get("expires_in", 3600) - 60, 60))
    return data["access_token"]


def _get(url, params=None):
    resp = requests.get(url, headers={"Authorization": f"Bearer {_app_token()}"}, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _to_artist(item):
    return {
        "external_id": item["id"],
        "name": item["name"],
        "genres": item.get("genres", []),
        "image_url": item["images"][0]["url"] if item.get("images") else None,
    }


def search_artists(query, limit=8):
    query = query.strip()
    if len(query) < 2:
        return []
    data = _get(SEARCH_URL, {"q": query[:100], "type": "artist", "limit": limit})
    return [_to_artist(item) for item in data["artists"]["items"]]


def get_artist(artist_id):
    return _to_artist(_get(ARTIST_URL.format(id=artist_id)))


TRACK_URL = "https://api.spotify.com/v1/tracks/{id}"


def _to_track(item):
    images = item.get("album", {}).get("images") or []
    return {
        "track_id": item["id"],
        "track_name": item["name"],
        "artist_name": item["artists"][0]["name"] if item.get("artists") else "",
        "image_url": images[-1]["url"] if images else None,
    }


def search_tracks(query, limit=6):
    query = query.strip()
    if len(query) < 2:
        return []
    data = _get(SEARCH_URL, {"q": query[:100], "type": "track", "limit": limit})
    return [_to_track(item) for item in data["tracks"]["items"]]


def get_track(track_id):
    return _to_track(_get(TRACK_URL.format(id=track_id)))


ALBUM_URL = "https://api.spotify.com/v1/albums/{id}"


def _to_album(item):
    images = item.get("images") or []
    return {
        "album_id": item["id"],
        "album_name": item["name"],
        "artist_name": ", ".join(a["name"] for a in item.get("artists", [])[:2]),
        "image_url": images[-1]["url"] if images else None,
        "year": (item.get("release_date") or "")[:4],
    }


def search_albums(query, limit=6):
    query = query.strip()
    if len(query) < 2:
        return []
    data = _get(SEARCH_URL, {"q": query[:100], "type": "album", "limit": limit})
    return [_to_album(item) for item in data["albums"]["items"] if item]


def get_album(album_id):
    return _to_album(_get(ALBUM_URL.format(id=album_id)))


# --- Profile question answers: a song, an artist or an album, stored in the same shape -------------

def _answer_from(kind, item):
    """{track_id, track_name, artist_name, image_url} for any kind, so one table/template handles all."""
    if kind == "artist":
        return {"track_id": item["external_id"], "track_name": item["name"], "artist_name": "",
                "image_url": item["image_url"]}
    if kind == "album":
        return {"track_id": item["album_id"], "track_name": item["album_name"], "artist_name": item["artist_name"],
                "image_url": item["image_url"]}
    return item


def search_answers(kind, query):
    if kind == "artist":
        return [_answer_from(kind, a) for a in search_artists(query, limit=6)]
    if kind == "album":
        return [_answer_from(kind, a) for a in search_albums(query)]
    return search_tracks(query)


def get_answer(kind, item_id):
    if kind == "artist":
        return _answer_from(kind, get_artist(item_id))
    if kind == "album":
        return _answer_from(kind, get_album(item_id))
    return get_track(item_id)

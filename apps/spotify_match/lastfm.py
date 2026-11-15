import requests
from django.conf import settings

from .models import SOURCE_LASTFM
from . import services

API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_NOT_FOUND = 6
TAGGED_ARTISTS = 20


class LastfmError(Exception):
    pass


def is_configured():
    return bool(settings.LASTFM_API_KEY)


def _call(method, **params):
    resp = requests.get(
        API_URL,
        params={"method": method, "api_key": settings.LASTFM_API_KEY, "format": "json", **params},
        timeout=10,
    )
    data = resp.json() if resp.content else {}
    if "error" in data:
        raise LastfmError(data.get("message", "Last.fm error"), data["error"])
    resp.raise_for_status()
    return data


def user_exists(username):
    try:
        _call("user.getinfo", user=username)
    except LastfmError as e:
        if len(e.args) > 1 and e.args[1] == USER_NOT_FOUND:
            return False
        raise
    return True


def _as_list(value):
    # Last.fm returns a bare object instead of a list when there is one item.
    if isinstance(value, dict):
        return [value]
    return value or []


def artist_tags(name):
    try:
        data = _call("artist.gettoptags", artist=name, autocorrect=1)
    except (LastfmError, requests.exceptions.RequestException):
        return []
    tags = _as_list(data.get("toptags", {}).get("tag"))
    return [t["name"].lower() for t in tags[:3] if t.get("name")]


def sync_lastfm_taste(user, limit=50, period="6month"):
    artists_data = _call("user.gettopartists", user=user.lastfm_username, limit=limit, period=period)
    tracks_data = _call("user.gettoptracks", user=user.lastfm_username, limit=limit, period=period)

    artists = []
    for i, item in enumerate(_as_list(artists_data.get("topartists", {}).get("artist"))):
        artists.append({
            "external_id": (item.get("mbid") or item["name"])[:255],
            "name": item["name"],
            "genres": artist_tags(item["name"]) if i < TAGGED_ARTISTS else [],
            "image_url": None,
        })

    tracks = [
        {
            "external_id": (item.get("mbid") or f'{item["artist"]["name"]} - {item["name"]}')[:255],
            "name": item["name"],
            "artist_name": item.get("artist", {}).get("name", ""),
        }
        for item in _as_list(tracks_data.get("toptracks", {}).get("track"))
    ]

    services.replace_source_artists(user, SOURCE_LASTFM, _dedupe(artists))
    services.replace_source_tracks(user, SOURCE_LASTFM, _dedupe(tracks))


def _dedupe(rows):
    seen, result = set(), []
    for row in rows:
        if row["external_id"] not in seen:
            seen.add(row["external_id"])
            result.append(row)
    return result

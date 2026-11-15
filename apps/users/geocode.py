"""GPS -> city name, via OpenStreetMap's free Nominatim service. (Location is GPS-only; nobody types a city.)"""
import logging
import time

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy asks for an identifying User-Agent and at most ~1 request/second;
# rounding to ~1 km and caching for a week keeps us far under that and avoids storing exact positions.
USER_AGENT = "MusicLuvers/0.1 (city lookup for profile)"
CACHE_SECONDS = 7 * 24 * 3600


def rounded(lat, lon):
    """~1 km precision: enough for "8 km away", not enough to find someone's home."""
    return round(lat, 2), round(lon, 2)


def _get(url, params, what):
    """GET with one retry (phone networks and this Mac's Wi-Fi drop the odd request). None on failure."""
    for attempt in (1, 2):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=6)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("%s failed (attempt %s): %s", what, attempt, exc)
            if attempt == 1:
                time.sleep(1)
    return None


def city_for(lat, lon):
    """GPS -> e.g. "Lagos, Nigeria", or None when the lookup fails."""
    lat, lon = rounded(lat, lon)
    key = f"geocode:{lat}:{lon}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    data = _get(REVERSE_URL, {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "accept-language": "en"},
                f"City lookup for {lat},{lon}")
    if data is None:
        return None
    address = data.get("address", {})
    place = (address.get("city") or address.get("town") or address.get("village")
             or address.get("county") or address.get("state"))
    label = ", ".join(p for p in (place, address.get("country")) if p)[:255]
    cache.set(key, label, CACHE_SECONDS)
    return label or None


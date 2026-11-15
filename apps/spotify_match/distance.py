"""Distance between two people, from their rough (~1 km) positions."""
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def km_between(a, b):
    """Great-circle distance in km, or None when either person has no position."""
    if not (a.has_position and b.has_position):
        return None
    lat1, lon1, lat2, lon2 = map(radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(h))


def within_range(km, limit_km):
    """limit 0 means "Anywhere". Unknown distance is allowed (we can't tell, so don't hide them)."""
    return km is None or not limit_km or km <= limit_km


def label(km):
    """Rounded on purpose, so nobody can work out where someone lives."""
    if km is None:
        return ""
    if km < 2:
        return "Less than 2 km away"
    if km < 10:
        return f"{round(km)} km away"
    return f"{int(round(km / 5.0) * 5)} km away"

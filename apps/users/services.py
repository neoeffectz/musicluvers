from .models import CustomUser
from django.utils import timezone
import base64
from django.conf import settings
from django.utils.dateparse import parse_date

TOKEN_URL = "https://accounts.spotify.com/api/token"


def _basic_auth_header():
    raw = f"{settings.SPOTIFY_CLIENT_ID}:{settings.SPOTIFY_CLIENT_SECRET}".encode()
    return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}


def update_profile(user, data):
    """Saves the valid fields and keeps the old value for anything invalid. Returns a list of problems
    to show the user (empty when everything saved)."""
    from datetime import date
    from .forms import MIN_AGE
    fields = ["birthdate", "location", "gender", "preference", "max_distance_km"]  # no bio: profiles are music-only
    errors = []
    for f in fields:
        if f not in data:
            continue
        value = data[f]
        if f == "birthdate":
            try:
                value = parse_date(value) if value else None
            except ValueError:
                value = None
            if value is None:
                continue  # blank or garbled: keep the birthday we have
            today = date.today()
            age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
            if age < MIN_AGE:
                errors.append(f"You need to be {MIN_AGE} or older to use Music Luvers.")
                continue
            if age > 100:
                errors.append("Check your birthday.")
                continue
        elif f == "gender" and value not in dict(user.GENDER_CHOICES):
            continue  # never wipe gender: a blank one would hide the person from everyone
        elif f == "preference" and value not in dict(user.PREFERENCE_CHOICES):
            continue
        elif f == "max_distance_km":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value not in dict(user.DISTANCE_CHOICES):
                continue
        elif f == "location":
            value = " ".join(str(value).split())[:255]
        setattr(user, f, value)
    before = CustomUser.objects.filter(id=user.id).values("gender", "preference").first() or {}
    user.save(update_fields=fields)
    if (before.get("gender"), before.get("preference")) != (user.gender, user.preference) and user.top_artists.exists():
        # Who can see whom changed: score the people they can now meet.
        from apps.spotify_match.services import publish_taste_change
        publish_taste_change(user)
    return errors


def add_profile_photo(user, image_file):
    from .models import ProfilePhoto
    rank = user.photos.count()
    photo = ProfilePhoto.objects.create(user=user, image=image_file, rank=rank)
    user.forget_photo()
    return photo


def delete_profile_photo(user, photo_id):
    user.photos.filter(id=photo_id).delete()
    user.forget_photo()

# LOCATION

MOVED_KM = 15  # only rename someone's city after a real move, not GPS jitter


def set_position(user, lat, lon, lookup_city=True):
    """Saves the rough GPS position. Fills/updates the city name when it's empty or the person moved
    city; returns the city name (None if the lookup failed, which is fine — the position is saved)."""
    from apps.spotify_match import distance
    from . import geocode

    lat, lon = geocode.rounded(lat, lon)
    before = CustomUser(latitude=user.latitude, longitude=user.longitude)
    user.latitude, user.longitude, user.location_updated_at = lat, lon, timezone.now()
    fields = ["latitude", "longitude", "location_updated_at"]
    moved = distance.km_between(before, user)
    city = None
    if lookup_city and (not user.location or moved is None or moved > MOVED_KM):
        city = geocode.city_for(lat, lon)
        if city:
            user.location = city
            fields.append("location")
    user.save(update_fields=fields)
    if not user.location:
        _fill_city_later(user)
    return city


def _fill_city_later(user):
    """City lookup failed: retry in the background once this request's data is saved."""
    import logging
    from django.db import transaction
    from .tasks import fill_city_name

    def enqueue():
        try:
            fill_city_name.delay(str(user.id))
        except Exception:  # broker down: the next app open retries anyway
            logging.getLogger(__name__).exception("Couldn't queue city lookup for %s", user.id)
    transaction.on_commit(enqueue)


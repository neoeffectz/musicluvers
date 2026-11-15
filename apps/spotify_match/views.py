import logging
from functools import wraps
import re
import requests
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from . import artist_search, lastfm, notifications, services, taste_catalog
from .models import ProfilePrompt
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from apps.users.models import CustomUser
from apps.events import bus, catalog
from apps.users import analytics

logger = logging.getLogger(__name__)


def post_or_redirect(to):
    """Form-only URLs: a refresh or back-button GET goes back to the page instead of a 405."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method != "POST":
                return redirect(to)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator



@ensure_csrf_cookie
@login_required(login_url="users:home")
def dashboard(request):
    user = request.user
    if not user.top_artists.exists() and not user.has_connected_taste_source():
        return redirect("spotify_match:build_taste")

    default_tab = "sound" if not user.has_seen_taste_intro else "discover"
    if request.GET.get("tab") in ("discover", "sound", "profile"):
        default_tab = request.GET["tab"]
    if not user.has_seen_taste_intro:
        user.has_seen_taste_intro = True
        user.save(update_fields=["has_seen_taste_intro"])

    stats = services.taste_stats(user)
    # Draw the first tab into the page now, instead of an empty page that immediately asks for it
    # (that second request cost a full extra round trip on every open).
    from django.template.loader import render_to_string
    if default_tab == "sound":
        tab_html = render_to_string("spotify_match/partials/my_sound.html", _my_sound_context(request, stats), request=request)
    elif default_tab == "profile":
        tab_html = render_to_string("users/partials/_profile_tab.html", {"user": user}, request=request)
    else:
        tab_html = render_to_string("spotify_match/partials/discover_card.html", _discover_context(request, _next_card(request)), request=request)
    return render(request, "spotify_match/dashboard.html", {
        "default_tab": default_tab,
        "initial_tab_html": tab_html,
        "greeting": services.get_greeting(),
        "matches": services.get_matches(user),
        **stats,
    })


def _my_sound_context(request, stats=None):
    user = request.user
    stats = stats or services.taste_stats(user)
    prompts = services.prompts_with_answers(user)
    return {
        "user": user,
        "display_name": user.first_name or user.username,
        "prompts": prompts,
        "answered_count": sum(1 for p in prompts if p["answer"]),
        **stats,
    }


@login_required(login_url="users:home")
def my_sound_tab(request):
    return render(request, "spotify_match/partials/my_sound.html", _my_sound_context(request))


def _discover_context(request, card):
    """When Discover runs out, offer people beyond the distance limit (like Tinder's "show people further away")."""
    context = {"card": card, "expanded": request.session.get("discover_expand", False)}
    if card is None and not context["expanded"]:
        context["further_away"] = services.count_further_away(request.user)
    return context


def _next_card(request):
    feed = services.get_discovery_feed(request.user, limit=1, expand=request.session.get("discover_expand", False))
    return feed[0] if feed else None


@login_required(login_url="users:home")
def discover_tab(request):
    if request.GET.get("expand") == "1":
        request.session["discover_expand"] = True
    return render(request, "spotify_match/partials/discover_card.html", _discover_context(request, _next_card(request)))


@login_required(login_url="users:home")
def notifications_badge(request):
    return render(request, "spotify_match/partials/_notification_badge.html",
                  {"unread": notifications.unread_count(request.user)})


@login_required(login_url="users:home")
def notifications_panel(request):
    """Opening the panel shows the latest notifications and marks them all read."""
    items = notifications.items_for(request.user)
    notifications.mark_all_read(request.user)
    response = render(request, "spotify_match/partials/_notification_panel.html", {"items": items})
    response["HX-Trigger"] = "notificationsChanged"
    return response



@login_required(login_url="users:home")
@require_POST
def swipe_partial(request):
    to_user_id = request.POST.get("to_user_id")
    liked = request.POST.get("liked") == "true"
    try:
        result = services.record_swipe(request.user, to_user_id, liked)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    context = _discover_context(request, _next_card(request))
    if result["matched"]:
        context["matched_user"] = result["matched_user"]
        context["matches"] = services.get_matches(request.user)

    response = render(request, "spotify_match/partials/swipe_result.html", context)
    analytics.track_response(response, "liked" if liked else "passed")
    if result["matched"]:
        analytics.track_response(response, "matched")
    return response


@login_required(login_url="users:home")
def user_detail(request, user_id):
    other_user = get_object_or_404(CustomUser, id=user_id)
    card = services.get_user_profile_view(request.user, other_user)
    if card is None:
        return redirect("spotify_match:dashboard")
    context = {"card": card}
    context.update(services.get_sidebar_context(request.user))
    return render(request, "spotify_match/user_detail.html", context)

@login_required(login_url="users:home")
def all_matches(request):
    matches = services.get_matches(request.user)
    context = services.get_sidebar_context(request.user, matches=matches)
    return render(request, "spotify_match/all_matches.html", context)

@login_required(login_url="users:home")
@require_POST
def unmatch_view(request, user_id):
    other_user = get_object_or_404(CustomUser, id=user_id)
    services.unmatch(request.user, other_user)
    return redirect("spotify_match:all_matches")


SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{1,64}$")
PASSED_ARTISTS_SESSION_KEY = "passed_artist_ids"
MAX_PASSED_ARTISTS = 300
LASTFM_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,14}$")


def _build_taste_context(user, **extra):
    picks = services.manual_artists(user)
    return {
        "picks": picks,
        "picked_ids": [p.external_id for p in picks],
        "min_picks": services.MIN_MANUAL_ARTISTS,
        "lastfm_enabled": lastfm.is_configured(),
        "genres": taste_catalog.GENRES,
        "max_genres": taste_catalog.MAX_GENRES,
        **extra,
    }


def _catalog_artist_with_genres(artist_id):
    artist = artist_search.get_artist(artist_id)
    if not artist["genres"]:
        artist["genres"] = taste_catalog.genres_for_artist(artist["name"])
    return artist


def _picks_partial(request, error=None):
    return render(request, "spotify_match/partials/_taste_picks.html",
                  _build_taste_context(request.user, error=error))


@ensure_csrf_cookie
@login_required(login_url="users:home")
def build_taste(request):
    user = request.user
    step = "genres" if not user.favorite_genres or request.GET.get("step") == "genres" else "artists"
    return render(request, "spotify_match/build_taste.html", _build_taste_context(user, step=step))


@login_required(login_url="users:home")
@post_or_redirect("spotify_match:build_taste")
def taste_genres(request):
    genres = taste_catalog.clean_genre_keys(request.POST.getlist("genres"))
    if not genres:
        return render(request, "spotify_match/build_taste.html", _build_taste_context(
            request.user, step="genres", error="Pick at least one genre.",
        ), status=400)
    services.set_favorite_genres(request.user, genres)
    return redirect("spotify_match:build_taste")


@login_required(login_url="users:home")
def taste_suggestions(request):
    user = request.user
    try:
        artists = taste_catalog.suggestions_for_genres(user.favorite_genres)
    except requests.exceptions.RequestException:
        logger.exception("Artist suggestions failed")
        artists = []
    picked = set(services.manual_artists(user).values_list("external_id", flat=True))
    return render(request, "spotify_match/partials/_artist_grid.html", {
        "artists": artists, "picked": picked,
    })


@login_required(login_url="users:home")
def taste_search(request):
    try:
        results = artist_search.search_artists(request.GET.get("q", ""))
        error = None
    except requests.exceptions.RequestException:
        logger.exception("Artist search failed")
        results, error = [], "Search is unavailable right now. Try again in a moment."
    picked = set(services.manual_artists(request.user).values_list("external_id", flat=True))
    return render(request, "spotify_match/partials/_taste_search_results.html", {
        "results": results, "picked": picked, "error": error,
    })


@login_required(login_url="users:home")
@require_POST
def taste_add(request):
    artist_id = request.POST.get("artist_id", "")
    if not SPOTIFY_ID.match(artist_id):
        return HttpResponseBadRequest("Invalid artist.")
    existing = services.manual_artists(request.user).filter(external_id=artist_id).first()
    if existing and request.POST.get("toggle"):
        services.remove_manual_artist(request.user, existing.id)
        return _picks_partial(request)
    try:
        services.add_manual_artist(request.user, _catalog_artist_with_genres(artist_id))
    except requests.exceptions.RequestException:
        logger.exception("Artist lookup failed")
        return _picks_partial(request, "Couldn't add that artist. Try again.")
    except ValueError as e:
        return _picks_partial(request, str(e))
    return _picks_partial(request)


@login_required(login_url="users:home")
@require_POST
def taste_remove(request, artist_id):
    services.remove_manual_artist(request.user, artist_id)
    return _picks_partial(request)


@login_required(login_url="users:home")
@post_or_redirect("spotify_match:build_taste")
def taste_finish(request):
    try:
        services.finish_manual_taste(request.user)
    except ValueError as e:
        return render(request, "spotify_match/build_taste.html",
                      _build_taste_context(request.user, step="artists", error=str(e)), status=400)
    analytics.track(request, "artists_added", artists=request.user.top_artists.count())
    if not request.user.prompts.exists():
        return redirect("spotify_match:taste_songs")
    return redirect("spotify_match:dashboard")


@login_required(login_url="users:home")
@post_or_redirect("spotify_match:build_taste")
def connect_lastfm(request):
    user = request.user
    username = request.POST.get("lastfm_username", "").strip()

    def fail(message):
        return render(request, "spotify_match/build_taste.html",
                      _build_taste_context(user, step="artists", lastfm_error=message, lastfm_username=username),
                      status=400)

    if not lastfm.is_configured():
        return fail("Last.fm isn't set up on this server yet.")
    if not LASTFM_USERNAME.match(username):
        return fail("That doesn't look like a Last.fm username.")
    try:
        if not lastfm.user_exists(username):
            return fail("We couldn't find that Last.fm user.")
    except (lastfm.LastfmError, requests.exceptions.RequestException):
        logger.exception("Last.fm lookup failed")
        return fail("Last.fm isn't responding. Try again in a moment.")

    with transaction.atomic():
        user.lastfm_username = username
        user.save(update_fields=["lastfm_username"])
        bus.publish(catalog.TASTE_SOURCE_CONNECTED, user_id=str(user.id), source="lastfm")
    analytics.track(request, "lastfm_connected")
    return redirect("spotify_match:dashboard")


@login_required(login_url="users:home")
@require_POST
def resync_taste(request):
    sources = services.request_taste_resync(request.user)
    return render(request, "spotify_match/partials/_resync_status.html", {"sources": sources})


# MUSIC PROMPTS


def _prompt_card(request, key, error=None):
    labels = services.prompt_labels()
    answer = request.user.prompts.filter(prompt=key).first()
    return render(request, "spotify_match/partials/_prompt_card.html", {
        "p": {"key": key, "label": labels[key], "answer": answer, "kind": services.prompt_kind(key)}, "error": error,
    })


@ensure_csrf_cookie
@login_required(login_url="users:home")
def taste_songs(request):
    return render(request, "spotify_match/songs.html", {
        "prompts": services.prompts_with_answers(request.user, keys=ProfilePrompt.SIGNUP_PROMPTS),
    })


@login_required(login_url="users:home")
def song_search(request):
    key = request.GET.get("prompt", "")
    if key not in services.prompt_labels():
        return HttpResponseBadRequest("Unknown prompt.")
    try:
        results = artist_search.search_answers(services.prompt_kind(key), request.GET.get("q", ""))
        error = None
    except requests.exceptions.RequestException:
        logger.exception("Answer search failed")
        results, error = [], "Search is unavailable right now. Try again in a moment."
    return render(request, "spotify_match/partials/_song_results.html", {
        "results": results, "key": key, "kind": services.prompt_kind(key), "error": error,
    })


@login_required(login_url="users:home")
@require_POST
def song_set(request):
    key = request.POST.get("prompt", "")
    track_id = request.POST.get("track_id", "")
    if key not in services.prompt_labels() or not SPOTIFY_ID.match(track_id):
        return HttpResponseBadRequest("Invalid song.")
    kind = services.prompt_kind(key)
    try:
        services.set_prompt(request.user, key, artist_search.get_answer(kind, track_id))
    except requests.exceptions.RequestException:
        logger.exception("Answer lookup failed")
        return _prompt_card(request, key, f"Couldn't save that {kind}. Try again.")
    services.rescore_if_ready(request.user)  # same song as someone = higher score, live
    return _prompt_card(request, key)


@login_required(login_url="users:home")
@require_POST
def song_clear(request, prompt):
    if prompt not in services.prompt_labels():
        return HttpResponseBadRequest("Unknown prompt.")
    services.clear_prompt(request.user, prompt)
    services.rescore_if_ready(request.user)
    return _prompt_card(request, prompt)


@login_required(login_url="users:home")
@post_or_redirect("spotify_match:taste_songs")
def songs_done(request):
    user = request.user
    if user.prompts.exists() and user.top_artists.exists():
        services.publish_taste_change(user)
    analytics.track(request, "songs_added", songs=user.prompts.count())
    return redirect("spotify_match:dashboard")


# ARTIST SWIPE DECK


def _next_deck_artist(request):
    user = request.user
    passed = request.session.get(PASSED_ARTISTS_SESSION_KEY, [])
    known = set(user.top_artists.values_list("external_id", flat=True)) | set(passed)
    genres = user.favorite_genres or [key for key, _, _ in taste_catalog.GENRES]
    try:
        for genre in genres:
            for artist in taste_catalog.suggestions_for_genre(genre):
                if artist["external_id"] not in known:
                    return {**artist, "genre_label": taste_catalog.GENRE_LABELS[genre]}
    except requests.exceptions.RequestException:
        logger.exception("Artist deck failed")
    return None


def _deck_partial(request, error=None):
    return render(request, "spotify_match/partials/_artist_swipe.html", {
        "artist": _next_deck_artist(request), "error": error,
    })


@login_required(login_url="users:home")
def artist_deck(request):
    return _deck_partial(request)


@login_required(login_url="users:home")
@require_POST
def artist_swipe(request):
    artist_id = request.POST.get("artist_id", "")
    if not SPOTIFY_ID.match(artist_id):
        return HttpResponseBadRequest("Invalid artist.")
    if request.POST.get("liked") == "true":
        try:
            services.add_manual_artist(request.user, _catalog_artist_with_genres(artist_id))
        except requests.exceptions.RequestException:
            logger.exception("Artist lookup failed")
            return _deck_partial(request, "Couldn't add that artist. Try again.")
        except ValueError as e:
            return _deck_partial(request, str(e))
        services.rescore_if_ready(request.user)
    else:
        passed = request.session.get(PASSED_ARTISTS_SESSION_KEY, [])
        request.session[PASSED_ARTISTS_SESSION_KEY] = (passed + [artist_id])[-MAX_PASSED_ARTISTS:]
    return _deck_partial(request)

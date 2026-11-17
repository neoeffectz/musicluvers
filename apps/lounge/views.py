import logging

import requests
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from apps.spotify_match import artist_search
from apps.spotify_match import services as match
from apps.spotify_match.views import SPOTIFY_ID
from apps.users import analytics
from apps.users.models import CustomUser

from . import schedule, services

logger = logging.getLogger(__name__)
JOINED_KEY = "lounge_joined_on"


@ensure_csrf_cookie  # people often land here straight from the Friday notification
@login_required(login_url="users:home")
def lounge_page(request):
    context = {"is_open": schedule.is_open(), "next_opening": schedule.next_opening()}
    if context["is_open"]:
        services.check_in(request.user)
        context.update(services.stage(request.user))
        context["people"] = services.people_here()
        today = str(schedule.session_date())
        if request.session.get(JOINED_KEY) != today:
            request.session[JOINED_KEY] = today
            analytics.track(request, "lounge_joined")
    context.update(match.get_sidebar_context(request.user))
    return render(request, "lounge/lounge.html", context)


@login_required(login_url="users:home")
def stage_partial(request):
    if not schedule.is_open():
        # Just closed: reload the page so everyone sees the "closed" screen straight away
        return HttpResponse(headers={"HX-Refresh": "true"})
    return render(request, "lounge/partials/_stage.html", services.stage(request.user))


@login_required(login_url="users:home")
def search(request):
    query = request.GET.get("q", "")
    if len(query.strip()) < 3:   # wait for a few letters: fewer requests to Spotify
        return render(request, "lounge/partials/_search_results.html", {"results": [], "error": None})
    try:
        results, error = artist_search.search_tracks(query), None
    except requests.exceptions.RequestException as e:
        if not isinstance(e, artist_search.SpotifyBusy):
            logger.exception("Lounge song search failed")
        # Spotify busy or down: songs the Lounge already knows still come up
        results = services.search_known_songs(query)
        error = None if results else "Search is busy right now. Try again in a few minutes."
    return render(request, "lounge/partials/_search_results.html", {"results": results, "error": error})


@login_required(login_url="users:home")
@require_POST
def request_song(request):
    track_id = request.POST.get("track_id", "")
    if not SPOTIFY_ID.match(track_id):
        return HttpResponseBadRequest("Couldn't find that song. Try another one.", content_type="text/plain")
    try:
        services.request_song(request.user, track_id)
    except ValueError as e:
        return HttpResponseBadRequest(str(e), content_type="text/plain")
    except requests.exceptions.RequestException:
        logger.exception("Lounge request lookup failed")
        return HttpResponse("Couldn't reach Spotify. Try again.", status=502, content_type="text/plain")
    response = render(request, "lounge/partials/_my_request.html", services.stage(request.user))
    return analytics.track_response(response, "lounge_song_requested")


@login_required(login_url="users:home")
def person(request, user_id):
    other = get_object_or_404(CustomUser, id=user_id)
    card = services.person_card(request.user, other)
    if card is None:
        return HttpResponse("That profile isn't available.", status=404, content_type="text/plain")
    response = render(request, "lounge/partials/_person.html", {"card": card})
    return analytics.track_response(response, "lounge_profile_opened")


@login_required(login_url="users:home")
@require_POST
def like(request, user_id):
    other = get_object_or_404(CustomUser, id=user_id)
    card = services.person_card(request.user, other)
    if card is None or not card["can_like"]:
        return HttpResponseBadRequest("You can't like this person.", content_type="text/plain")
    result = match.record_swipe(request.user, other.id, True)
    response = render(request, "lounge/partials/_like_result.html", {"other": other, "matched": result["matched"]})
    analytics.track_response(response, "lounge_liked")
    if result["matched"]:
        analytics.track_response(response, "matched", source="lounge")
    return response

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth import login
from . import accounts, analytics, geocode, safety, services
from .forms import AboutYouForm, LoginForm, PhotoForm, SignupForm

logger = logging.getLogger(__name__)


def home(request):
    if request.user.is_authenticated:
        return redirect("spotify_match:dashboard")
    return render(request, "users/home.html", {
        "signup_form": SignupForm(),
        "login_form": LoginForm(request),
        "mode": "login" if request.GET.get("login") else "signup",
        "deleted": request.GET.get("deleted") == "1",
    })


def signup(request):
    if request.method != "POST":
        return redirect("users:home")
    form = SignupForm(request.POST)
    if not form.is_valid():
        return render(request, "users/home.html", {
            "signup_form": form, "login_form": LoginForm(request), "mode": "signup",
        }, status=400)
    user = form.save()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    accounts.send_verification(request, user)
    analytics.track(request, "signed_up")
    return redirect("users:about_you")


@login_required(login_url="users:home")
def about_you(request):
    user = request.user
    if request.method == "POST":
        form = AboutYouForm(request.POST)
        location_error = None if user.has_position else "Share your location to continue. We use it to show you people nearby."
        if form.is_valid() and not location_error:
            data = form.cleaned_data
            services.update_profile(user, {
                "gender": data["gender"],
                "preference": data["preference"],
                "birthdate": data["birthdate"].isoformat(),
            })
            analytics.track(request, "profile_completed")
            return redirect("spotify_match:build_taste")
        status = 400
    else:
        form = AboutYouForm(initial={
            "gender": user.gender, "preference": user.preference, "birthdate": user.birthdate,
        })
        location_error = None
        status = 200
    return render(request, "users/about_you.html", {
        "form": form, "user": user, "photo": user.photos.first(), "location_error": location_error,
    }, status=status)


@login_required(login_url="users:home")
@require_POST
def about_you_photo(request):
    """Uploads straight away so the user sees the stored photo, not just a local preview."""
    upload = request.FILES.get("photo")
    logger.info(
        "Photo upload from %s: %s",
        request.user.id,
        f"{upload.name} {upload.content_type} {upload.size} bytes" if upload else "no file in request",
    )
    form = PhotoForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = form.errors.get("photo") or ["Pick a photo first."]
        return JsonResponse({"error": errors[0]}, status=400)
    try:
        photo = services.add_profile_photo(request.user, form.cleaned_data["photo"])
    except Exception:
        logger.exception("Photo upload to storage failed")
        return JsonResponse({"error": "Couldn't upload that photo. Try another one."}, status=502)
    replace_id = request.POST.get("replace_id")
    if replace_id:
        request.user.photos.exclude(id=photo.id).filter(id=replace_id).delete()
        request.user.forget_photo()
    return JsonResponse({"id": str(photo.id), "url": photo.image.url})


@login_required(login_url="users:home")
@require_POST
def locate(request):
    """POST lat/lon from the phone's GPS. Always saves the rough position; also returns the city name
    when the lookup works ({"saved": true, "city": "Lagos, Nigeria"} or "city": null)."""
    try:
        lat, lon = float(request.POST["lat"]), float(request.POST["lon"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "Missing location."}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({"error": "That location doesn't look right."}, status=400)
    city = services.set_position(request.user, lat, lon, lookup_city=request.POST.get("quiet") != "1")
    return JsonResponse({"saved": True, "city": city or request.user.location or None})



def login_view(request):
    if request.method != "POST":
        return redirect("users:home")
    form = LoginForm(request, request.POST)
    if not form.is_valid():
        return render(request, "users/home.html", {
            "signup_form": SignupForm(), "login_form": form, "mode": "login",
        }, status=400)
    login(request, form.user, backend="django.contrib.auth.backends.ModelBackend")
    analytics.track(request, "logged_in")
    return redirect("spotify_match:dashboard")


@login_required(login_url="users:home")
def profile_tab(request):
    return render(request, "users/partials/_profile_tab.html", {"user": request.user})


@login_required(login_url="users:home")
@require_POST
def profile_update(request):
    old_distance = request.user.max_distance_km
    data = request.POST.copy()
    data.pop("location", None)  # location only comes from GPS (users:locate)
    errors = services.update_profile(request.user, data)
    if request.user.max_distance_km != old_distance:
        request.session.pop("discover_expand", None)
    if request.FILES.get("photo"):
        services.add_profile_photo(request.user, request.FILES["photo"])
    request.user.refresh_from_db()
    return render(request, "users/partials/_profile_tab.html", {
        "user": request.user, "errors": errors, "saved": not errors,
    })


@login_required(login_url="users:home")
@require_POST
def profile_photo_delete(request, photo_id):
    services.delete_profile_photo(request.user, photo_id)
    return render(request, "users/partials/_profile_tab.html", {"user": request.user})

# REPORT / BLOCK

def _safety_result(request, user_id, action):
    from django.shortcuts import get_object_or_404
    from .models import CustomUser
    other = get_object_or_404(CustomUser, id=user_id)
    context = {"name": other.first_name or other.username, "target_id": other.id}
    try:
        action(other)
    except ValueError as e:
        context["error"] = str(e)
    return context


@login_required(login_url="users:home")
@require_POST
def block_view(request, user_id):
    context = _safety_result(request, user_id, lambda other: safety.block(request.user, other))
    return render(request, "users/safety/_done.html", context, status=400 if "error" in context else 200)


@login_required(login_url="users:home")
@require_POST
def report_view(request, user_id):
    reason, details = request.POST.get("reason", ""), request.POST.get("details", "")
    context = _safety_result(request, user_id, lambda other: safety.report(request.user, other, reason, details))
    context["reported"] = "error" not in context
    return render(request, "users/safety/_done.html", context, status=400 if "error" in context else 200)


# EMAIL VERIFICATION

def verify_email(request, token):
    user = accounts.confirm_token(token)
    return render(request, "users/account/verified.html", {"ok": user is not None, "verified_user": user},
                  status=200 if user else 400)


@login_required(login_url="users:home")
@require_POST
def resend_verification(request):
    """Banner's "Resend" button. At most once a minute per account."""
    from django.core.cache import cache
    from django.http import HttpResponse
    key = f"verify-resend:{request.user.id}"
    if cache.get(key):
        return HttpResponse("Sent already. Wait a minute, then try again.")
    cache.set(key, 1, 60)
    sent = accounts.send_verification(request, request.user)
    return HttpResponse(f"Sent to {request.user.email}. Check your inbox." if sent else "Your email is already confirmed.")


# DELETE ACCOUNT

@login_required(login_url="users:home")
def delete_account(request):
    from django.contrib.auth import logout
    if request.method == "POST":
        if not request.user.check_password(request.POST.get("password", "")):
            return render(request, "users/account/delete.html", {"error": "That password isn't right."}, status=400)
        user = request.user
        logout(request)
        accounts.delete_account(user)
        return redirect("/?deleted=1")
    return render(request, "users/account/delete.html")


def terms(request):
    return render(request, "users/account/terms.html")


def privacy(request):
    return render(request, "users/account/privacy.html")


# PHONE NOTIFICATIONS + INSTALLABLE APP

@login_required(login_url="users:home")
@require_POST
def push_subscribe(request):
    import json
    from . import push
    try:
        push.save_subscription(request.user, json.loads(request.body or b"{}"), request.META.get("HTTP_USER_AGENT", ""))
    except (ValueError, json.JSONDecodeError) as e:
        return JsonResponse({"error": str(e) or "Bad request."}, status=400)
    return JsonResponse({"ok": True})


@login_required(login_url="users:home")
@require_POST
def push_unsubscribe(request):
    import json
    from . import push
    try:
        endpoint = json.loads(request.body or b"{}").get("endpoint", "")
    except json.JSONDecodeError:
        endpoint = ""
    push.remove_subscription(request.user, endpoint)
    return JsonResponse({"ok": True})


@login_required(login_url="users:home")
@require_POST
def push_test(request):
    """Profile → "Send a test notification"."""
    from . import push
    push.notify(request.user.id, "Music Luvers 🎵", "Notifications are on. We'll buzz you for matches and messages.",
                url="/dashboard/?tab=profile", tag="test")
    return JsonResponse({"ok": True})


def service_worker(request):
    """Served from the site root so it can handle notifications for every page."""
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    response = HttpResponse(render_to_string("users/pwa/sw.js"), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


def manifest(request):
    from django.templatetags.static import static
    return JsonResponse({
        "name": "Music Luvers",
        "short_name": "Music Luvers",
        "description": "Meet people who share your music taste.",
        "start_url": "/dashboard/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#101216",
        "theme_color": "#101216",
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")


@require_POST
def logout_view(request):
    from django.contrib.auth import logout
    logout(request)
    return redirect("/?login=1")

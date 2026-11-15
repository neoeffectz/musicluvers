from django.http import HttpResponse
from django.shortcuts import redirect


class HtmxRedirectMiddleware:
    """htmx follows redirects quietly and would drop a whole page (e.g. the login page after a session
    expires) inside a small box. For htmx requests, turn a redirect into a normal full-page change."""

    REDIRECTS = {301, 302, 303, 307, 308}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request") and response.status_code in self.REDIRECTS and response.get("Location"):
            full_page = HttpResponse(status=200)
            full_page["HX-Redirect"] = response["Location"]
            full_page.cookies = response.cookies
            return full_page
        return response


class FinishSignupMiddleware:
    """Sharing your location (plus gender and birthday) is required. Until it's done, app pages send people
    back to the "About you" step, so nobody can skip it by typing another address. (People without a
    location would otherwise show up for everyone, everywhere, because we can't tell how far away they are.)"""

    OPEN = ("/welcome/", "/logout/", "/login/", "/signup/", "/static/", "/admin/", "/api/", "/password/",
            "/verify-email/", "/account/", "/terms/", "/privacy/", "/push/", "/sw.js", "/manifest.webmanifest",
            "/favicon.ico")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and not user.is_staff
                and request.path != "/" and not request.path.startswith(self.OPEN)
                and not user.finished_about_you):
            return redirect("users:about_you")
        return self.get_response(request)

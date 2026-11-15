from django.http import HttpResponse


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

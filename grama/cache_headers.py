"""API answers are not stored by browsers unless a view says they are public (web audit WEB-HDR-002).

Answers with personal data (`/me/`, users, contact messages, service requests, the audit log, CSV
exports) used to carry no Cache-Control, so a browser could keep them on disk: on a shared office
computer they could be read after the Super Admin logged out. Every `/api/` answer now gets
`Cache-Control: no-store, private` unless its view already set a Cache-Control of its own. The
public answers keep theirs (`cache_control(public=True, max_age=…)`: the feed, banners, important
numbers, geography, services and bills), so the app and the website stay as fast on 2G/3G.

New endpoints are private by default; a public one says so with the `cache_control` decorator.
Pages and files Nginx serves itself (the website, the console, /media/) are not affected.
"""

from __future__ import annotations

API_PREFIX = "/api/"
PRIVATE = "no-store, private"


class PrivateApiAnswersMiddleware:
    """Near the top of MIDDLEWARE, so it also covers answers made by other middleware (lockout, CSRF)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(API_PREFIX) and not response.has_header("Cache-Control"):
            response["Cache-Control"] = PRIVATE
        return response

"""Console sessions: idle time counts only what the Super Admin does (BVTGC-72, audit CROSS-AUTH-001).

A console session ends after CONSOLE_SESSION_IDLE_MINUTES without a request (SESSION_COOKIE_AGE). Every
request renews it, as Django's SESSION_SAVE_EVERY_REQUEST would, except the console's own background
checks (new notifications, new service requests every 60 seconds). Those send the header
`X-Background-Request: 1` and leave the session's end where it was, so an unattended console ends on the
server too, not only in the page's code. App requests (JWT) carry no session and are not affected.
"""

from django.contrib.sessions.middleware import SessionMiddleware as DjangoSessionMiddleware

BACKGROUND_HEADER = "X-Background-Request"


def is_background(request) -> bool:
    return request.headers.get(BACKGROUND_HEADER, "").strip() == "1"


class SessionMiddleware(DjangoSessionMiddleware):
    def process_response(self, request, response):
        session = getattr(request, "session", None)
        if session is not None and not is_background(request):
            # Renew the session's end (cookie and database) on every request the person makes.
            session.modified = True
        return super().process_response(request, response)

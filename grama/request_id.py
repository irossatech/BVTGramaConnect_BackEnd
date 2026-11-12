"""A number for every request, in its answer and in every log line it causes (audit BE_02, BE-OBS-101).

Every answer carries `X-Request-ID`. Every log line written while the request runs shows the same
number, and a server error (500) gives it in its body, so a complaint ("it failed, number 3f9c…")
leads straight to the right lines in the server log. Nothing leaves the server.

A proxy may send its own X-Request-ID (letters, digits and dashes, 8 to 64 characters); it is kept,
so the nginx and Django logs share one number. Anything else is replaced with a new random number,
so a client cannot write arbitrary text into the logs.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

HEADER = "X-Request-ID"
_INCOMING = re.compile(r"[A-Za-z0-9-]{8,64}")

#: The current request's number; "-" outside a request (background jobs, commands).
current: ContextVar[str] = ContextVar("request_id", default="-")


def request_id_for(request) -> str:
    sent = request.META.get("HTTP_X_REQUEST_ID", "")
    return sent if _INCOMING.fullmatch(sent) else uuid.uuid4().hex


class RequestIdMiddleware:
    """First in MIDDLEWARE, so every answer, even an early error, carries the number."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request_id_for(request)
        token = current.set(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            current.reset(token)
        response[HEADER] = request.request_id
        return response


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record, for the log format (grama/settings.py LOGGING)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current.get()
        return True

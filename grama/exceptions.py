"""One error shape for the whole API (docs/api-contract.md, section 1):

    {"error": {"code": "...", "message": "...", "fields": {...}, ...extra}}

Raise ApiError for anything with a contract code. Raise AuthError for 401s,
so DRF keeps the WWW-Authenticate header and the 401 status.
"""

import math

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions, status


class ApiError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, message, *, status: int | None = None, fields=None, **extra):
        super().__init__(detail=message, code=code)
        if status is not None:
            self.status_code = status
        self.error_code = code
        self.fields = fields
        self.extra = extra


class AuthError(exceptions.AuthenticationFailed):
    """401 with a contract code: token_expired, token_invalid, session_revoked."""

    def __init__(self, code: str, message):
        super().__init__(detail=message, code=code)
        self.error_code = code


def permission_denied(message=None) -> ApiError:
    return ApiError(
        "permission_denied",
        message or _("Your role does not allow this."),
        status=status.HTTP_403_FORBIDDEN,
    )


def out_of_area(message=None) -> ApiError:
    return ApiError(
        "out_of_area",
        message or _("That area is outside your area."),
        status=status.HTTP_403_FORBIDDEN,
    )


_CODE_BY_TYPE = [
    # Django's own exceptions, which DRF converts to responses for us.
    (Http404, "not_found"),
    (DjangoPermissionDenied, "permission_denied"),
    (exceptions.NotAuthenticated, "not_authenticated"),
    (exceptions.AuthenticationFailed, "token_invalid"),
    (exceptions.PermissionDenied, "permission_denied"),
    (exceptions.NotFound, "not_found"),
    (exceptions.MethodNotAllowed, "method_not_allowed"),
    (exceptions.NotAcceptable, "not_acceptable"),
    (exceptions.UnsupportedMediaType, "unsupported_media"),
    (exceptions.Throttled, "throttled"),
    (exceptions.ParseError, "parse_error"),
]


def _first_message(data):
    if isinstance(data, dict):
        for value in data.values():
            found = _first_message(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _first_message(value)
            if found:
                return found
    elif data:
        return str(data)
    return None


def exception_handler(exc, context):
    # Imported here: rest_framework.views loads the authentication classes,
    # which import this module, so a top-level import would be circular.
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    fields = None
    extra = {}
    if isinstance(exc, (ApiError, AuthError)):
        code = exc.error_code
        message = str(exc.detail)
        fields = getattr(exc, "fields", None)
        extra = getattr(exc, "extra", {})
    elif isinstance(exc, exceptions.ValidationError):
        code = "validation_error"
        data = response.data
        fields = data if isinstance(data, dict) else {"non_field_errors": data}
        message = _first_message(fields) or str(_("Invalid input."))
    else:
        code = next((name for kind, name in _CODE_BY_TYPE if isinstance(exc, kind)), "error")
        detail = response.data.get("detail") if isinstance(response.data, dict) else response.data
        message = str(detail) if detail else str(exc)
        if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
            extra = {"retry_after_s": math.ceil(exc.wait)}  # also in the Retry-After header

    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    error.update(extra)
    response.data = {"error": error}
    return response

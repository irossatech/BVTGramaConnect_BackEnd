"""How API requests prove who they are.

- The mobile app sends a Bearer JWT (GramaJWTAuthentication).
- The web console uses a Django session cookie with CSRF protection
  (GramaSessionAuthentication). A session is created only by
  POST /api/v1/auth/session/login/ for users holding web_admin.access_web_console.

Both check token_version on every request, so blocking someone, resetting or
changing their password, or "log out everywhere" ends every token and session
at once (401 session_revoked). While must_change_password is set, every
endpoint except the ones in OPEN_WHILE_PASSWORD_CHANGE_REQUIRED answers 403
password_change_required.
"""

from django.contrib.auth import SESSION_KEY, logout
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from grama.exceptions import ApiError, AuthError

from .lockout import SESSION_TOKEN_VERSION
from .tokens import session_revoked, user_for_token

#: (view name, methods) still open while an official must change their password.
OPEN_WHILE_PASSWORD_CHANGE_REQUIRED = {
    "accounts:me": {"GET", "HEAD", "OPTIONS"},
    "accounts:password-change": {"POST", "OPTIONS"},
    "accounts:logout": {"POST", "OPTIONS"},
    "accounts:token-refresh": {"POST", "OPTIONS"},
    "accounts:session-logout": {"POST", "OPTIONS"},
}

#: The console permission. Sessions of users who lose it stop working.
CONSOLE_PERMISSION = "web_admin.access_web_console"


def password_change_required() -> ApiError:
    return ApiError(
        "password_change_required",
        _("Choose a new password before you continue."),
        status=status.HTTP_403_FORBIDDEN,
    )


def not_console_user() -> ApiError:
    return ApiError(
        "not_console_user",
        _("The web console is for Super Admins. Other officials use the mobile app."),
        status=status.HTTP_403_FORBIDDEN,
    )


def check_password_change(request, user) -> None:
    if user.must_change_password:
        match = getattr(request._request, "resolver_match", None)
        allowed = OPEN_WHILE_PASSWORD_CHANGE_REQUIRED.get(match.view_name if match else None, set())
        if request.method not in allowed:
            raise password_change_required()


class GramaJWTAuthentication(JWTAuthentication):
    """Bearer JWT, plus the token_version and blocked-user checks on every request.

    401 codes: token_expired (refresh and retry), token_invalid, session_revoked
    (log in again).
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            check_password_change(request, result[0])
        return result

    def get_validated_token(self, raw_token):
        try:
            return AccessToken(raw_token)
        except ExpiredTokenError:
            raise AuthError("token_expired", _("Access token expired. Refresh it and retry.")) from None
        except TokenError:
            raise AuthError("token_invalid", _("Access token is not valid.")) from None

    def get_user(self, validated_token):
        return user_for_token(validated_token)


class GramaSessionAuthentication(SessionAuthentication):
    """The console's Django session: CSRF on unsafe methods, token_version, console permission.

    A session whose user was blocked, reset or changed their password elsewhere
    is flushed and answers 401 session_revoked. An expired session (idle for
    SESSION_COOKIE_AGE) is simply gone: 401 not_authenticated.
    """

    def authenticate(self, request):
        django_request = request._request
        session = getattr(django_request, "session", None)
        had_login = session is not None and SESSION_KEY in session
        user = getattr(django_request, "user", None)
        if user is None or not user.is_authenticated:
            if had_login:
                # Django dropped the user: blocked, deleted, or the password changed.
                logout(django_request)
                raise session_revoked()
            return None
        if session.get(SESSION_TOKEN_VERSION) != user.token_version:
            logout(django_request)
            raise session_revoked()
        self.enforce_csrf(request)
        check_password_change(request, user)
        if not user.must_change_password:
            from . import authz

            if not authz.can(user, CONSOLE_PERMISSION):
                raise not_console_user()
        return (user, None)


def came_from_console(request) -> bool:
    """True when the request was authenticated by the console session."""
    return isinstance(getattr(request, "successful_authenticator", None), GramaSessionAuthentication)


def client_via(request) -> str:
    """'console' for the web console (its session, or the old X-Client headers), else 'app'. For audit entries."""
    if came_from_console(request):
        return "console"
    if request.headers.get("X-Client") == "web_admin" or request.headers.get("X-GC-Client") == "console":
        return "console"
    return "app"

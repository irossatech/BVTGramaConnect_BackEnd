from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from grama.exceptions import ApiError, AuthError

from .tokens import user_for_token

#: (view name, methods) still open while an official must change their password.
OPEN_WHILE_PASSWORD_CHANGE_REQUIRED = {
    "accounts:me": {"GET", "HEAD", "OPTIONS"},
    "accounts:password-change": {"POST", "OPTIONS"},
    "accounts:logout": {"POST", "OPTIONS"},
    "accounts:token-refresh": {"POST", "OPTIONS"},
}


def password_change_required() -> ApiError:
    return ApiError(
        "password_change_required",
        _("Choose a new password before you continue."),
        status=status.HTTP_403_FORBIDDEN,
    )


class GramaJWTAuthentication(JWTAuthentication):
    """Bearer JWT, plus the token_version and blocked-user checks on every request.

    401 codes: token_expired (refresh and retry), token_invalid, session_revoked
    (log in again). While must_change_password is set, every endpoint except
    the ones in OPEN_WHILE_PASSWORD_CHANGE_REQUIRED answers 403
    password_change_required.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None and result[0].must_change_password:
            match = getattr(request._request, "resolver_match", None)
            allowed = OPEN_WHILE_PASSWORD_CHANGE_REQUIRED.get(match.view_name if match else None, set())
            if request.method not in allowed:
                raise password_change_required()
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

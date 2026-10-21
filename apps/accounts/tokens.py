"""JWTs that can be revoked at once.

Every token carries the user's token_version in the "tv" claim. The
authentication class compares it with the database on every request, so
bumping token_version ends every session immediately, which a plain JWT
cannot do. All of the user's refresh tokens are blacklisted at the same time.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext as _
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from grama.exceptions import AuthError

from .models import User, UserStatus

TOKEN_VERSION_CLAIM = "tv"


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def session_revoked() -> AuthError:
    return AuthError("session_revoked", _("Your session has ended. Log in again."))


def user_for_token(token) -> User:
    """The token's user, if the token is still allowed to act for them."""
    try:
        user = User.objects.filter(pk=token.get(api_settings.USER_ID_CLAIM)).first()
    except (ValueError, TypeError, DjangoValidationError):
        user = None
    if user is None or user.is_blocked or token.get(TOKEN_VERSION_CLAIM) != user.token_version:
        raise session_revoked()
    return user


def rotate_refresh(raw_refresh: str) -> dict[str, str]:
    """New access and refresh tokens; the old refresh token is blacklisted."""
    try:
        refresh = RefreshToken(raw_refresh)
    except TokenError:
        raise session_revoked() from None
    user_for_token(refresh)

    data = {"access": str(refresh.access_token)}
    refresh.blacklist()
    refresh.set_jti()
    refresh.set_exp()
    refresh.set_iat()
    refresh.outstand()
    data["refresh"] = str(refresh)
    return data


def blacklist_refresh(raw_refresh: str) -> None:
    try:
        RefreshToken(raw_refresh).blacklist()
    except TokenError:
        pass


def revoke_all_sessions(user: User) -> None:
    """End every session of `user`: access tokens fail on their next use."""
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
        open_tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=token) for token in open_tokens], ignore_conflicts=True
        )
    user.refresh_from_db(fields=["token_version"])


def block_user(user: User) -> None:
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(status=UserStatus.BLOCKED)
        revoke_all_sessions(user)
    user.refresh_from_db(fields=["status", "token_version"])


def unblock_user(user: User) -> None:
    User.objects.filter(pk=user.pk).update(status=UserStatus.ACTIVE)
    user.refresh_from_db(fields=["status"])

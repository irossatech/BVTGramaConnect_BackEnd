"""JWTs (the mobile app) that can be revoked at once.

Every token carries the user's token_version in the "tv" claim. The
authentication class compares it with the database on every request, so
bumping token_version ends every session immediately, which a plain JWT
cannot do. All of the user's refresh tokens are blacklisted at the same time.
Console sessions store the same number (authentication.py), so they end too.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from grama.exceptions import AuthError

from .models import RefreshRotation, User

TOKEN_VERSION_CLAIM = "tv"
#: When the official last typed their password (Unix seconds). Renewals keep it, so it measures the
#: age of the login itself; after JWT_ABSOLUTE_DAYS renewal is refused (BVTGC-73).
LOGIN_TIME_CLAIM = "auth_time"


def _now() -> int:
    return int(timezone.now().timestamp())


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version
    refresh[LOGIN_TIME_CLAIM] = _now()
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _login_too_old(refresh) -> bool:
    """Has this login passed JWT_ABSOLUTE_DAYS? A token from before BVTGC-73 has no login time: its
    count starts now, at its first renewal after the deploy, so nobody is logged out on release day."""
    started = refresh.get(LOGIN_TIME_CLAIM)
    if started is None:
        refresh[LOGIN_TIME_CLAIM] = _now()
        return False
    days = settings.JWT_ABSOLUTE_DAYS
    return bool(days) and _now() - int(started) > days * 86400


def session_revoked() -> AuthError:
    return AuthError("session_revoked", _("Your session has ended. Log in again."))


def user_for_token(token) -> User:
    """The token's user, if the token is still allowed to act for them."""
    try:
        user = User.objects.filter(pk=token.get(api_settings.USER_ID_CLAIM)).first()
    except (ValueError, TypeError, DjangoValidationError):
        user = None
    if user is None or not user.is_active or token.get(TOKEN_VERSION_CLAIM) != user.token_version:
        raise session_revoked()
    return user


class _SentRefreshToken(RefreshToken):
    """A refresh token as sent to POST /auth/refresh/: signature, expiry, ID and type are checked here,
    the blacklist by rotate_refresh itself (_use_once, then _reissue), so a token whose answer was lost
    can get its grace (BVTGC-93). A token blacklisted any other way (logout, login age) has no rotation
    row and is refused there."""

    def check_blacklist(self) -> None:
        pass


def rotate_refresh(raw_refresh: str) -> dict[str, str]:
    """New access and refresh tokens; the old refresh token is blacklisted.

    A token used a moment ago comes back when the app never got the answer (a weak network). Within
    JWT_REFRESH_GRACE_SECONDS of its first use it still works (BVTGC-93): see _reissue.
    """
    try:
        refresh = _SentRefreshToken(raw_refresh)
    except TokenError:
        raise session_revoked() from None
    user_for_token(refresh)  # a block, a reset or "log out everywhere" ends the grace too
    if _login_too_old(refresh):
        _use_once(refresh)  # this token is spent too: the next try gets the same answer
        raise session_revoked()

    old_jti = refresh[api_settings.JTI_CLAIM]
    if not _use_once(refresh, allow_reuse=True):
        return _reissue(refresh, old_jti)
    data = _new_pair(refresh)
    RefreshRotation.objects.create(old_jti=old_jti, new_jti=refresh[api_settings.JTI_CLAIM])
    return data


def _new_pair(refresh) -> dict[str, str]:
    """An access token from `refresh`, then `refresh` itself turned into a new refresh token."""
    data = {"access": str(refresh.access_token)}
    refresh.set_jti()
    refresh.set_exp()
    refresh.set_iat()
    refresh.outstand()
    data["refresh"] = str(refresh)
    return data


def _reissue(refresh, old_jti: str) -> dict[str, str]:
    """The second chance for a refresh token already used (BVTGC-93).

    Only when it was replaced within the window and its replacement is still unused: never used for a
    refresh of its own and not logged out (both blacklist it). The replacement, whose answer was lost,
    ends here and a new pair is given, so one usable chain is left. Two such requests at once: the
    database's unique rule on the blacklist lets one through, the other answers session_revoked.
    """
    window = settings.JWT_REFRESH_GRACE_SECONDS
    if window <= 0:
        raise session_revoked()
    since = timezone.now() - timedelta(seconds=window)
    rotation = RefreshRotation.objects.filter(old_jti=old_jti, at__gte=since).first()
    replacement = rotation and OutstandingToken.objects.filter(jti=rotation.new_jti).first()
    if not replacement:
        raise session_revoked()
    try:
        with transaction.atomic():
            BlacklistedToken.objects.create(token=replacement)
            data = _new_pair(refresh)
            RefreshRotation.objects.filter(pk=rotation.pk).update(new_jti=refresh[api_settings.JTI_CLAIM])
    except IntegrityError:
        raise session_revoked() from None
    return data


def _use_once(refresh, *, allow_reuse: bool = False) -> bool:
    """Blacklist the refresh token being exchanged, so that only one request can use it (audit BE-DB-022).

    simplejwt's blacklist() uses get_or_create, so two requests with the same token at the same moment both
    passed and both got new tokens. A plain insert lets the database's unique rule pick one. The other
    request gets False with `allow_reuse` (rotate_refresh then tries the grace of BVTGC-93), else 401
    session_revoked, as for any used token.
    """
    token = OutstandingToken.objects.filter(jti=refresh[api_settings.JTI_CLAIM]).first()
    if token is None:  # issued outside issue_tokens(): keep simplejwt's way
        refresh.blacklist()
        return True
    try:
        with transaction.atomic():
            BlacklistedToken.objects.create(token=token)
    except IntegrityError:
        if allow_reuse:
            return False
        raise session_revoked() from None
    return True


def blacklist_refresh(raw_refresh: str) -> None:
    try:
        RefreshToken(raw_refresh).blacklist()
    except TokenError:
        pass


def revoke_all_sessions(user: User) -> None:
    """End every session of `user`: access tokens fail on their next use.

    Their phones are unlinked from push as well, as the app's own logout does (BVTGC-94): a lost or
    shared phone stops showing their alerts at once. The app links its phone again at the next login,
    and right after its own password change, so the phone in their hand keeps its alerts.
    """
    from apps.notifications.models import Device

    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
        open_tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=token) for token in open_tokens], ignore_conflicts=True
        )
        Device.objects.filter(user=user).delete()
    user.refresh_from_db(fields=["token_version"])


def block_user(user: User) -> None:
    """is_active=False and a token_version bump, in one transaction: every token and session ends at once."""
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(is_active=False)
        revoke_all_sessions(user)
    user.refresh_from_db(fields=["is_active", "token_version"])


def unblock_user(user: User) -> None:
    User.objects.filter(pk=user.pk).update(is_active=True)
    user.refresh_from_db(fields=["is_active"])

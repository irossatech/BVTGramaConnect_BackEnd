"""Passwords for officials: role defaults, invites, resets, login checks and throttling.

How it works:
- Each role has a default password, kept in RoleDefaultPassword. A role with no
  row gets a random one on first use; there is no default in the code.
- Inviting or resetting someone sets their password to their role's default
  and, with FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN, makes them choose their own
  at first login. An email tells them their username and the temporary password.
- Login accepts the username or the email, in any case. Failed tries are
  counted per username (LOGIN_MAX_FAILURES_PER_USERNAME in
  LOGIN_USERNAME_WINDOW_MINUTES, reset by a success) and per IP address
  (LOGIN_MAX_FAILURES_PER_IP per hour), in the database so the limits hold
  across processes.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import Throttled

from grama.exceptions import ApiError, permission_denied

from .models import LoginAttempt, RoleDefaultPassword, User, normalize_login
from .roles import ROLE_ORDER
from .scope import forget_areas, user_areas

#: No 0/O, 1/l/I: the password is read from an email and typed on a phone.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
HOUR = timedelta(hours=1)


def generate_password(length: int = 12) -> str:
    while True:
        password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in password) and any(c.islower() for c in password) and any(
            c.isupper() for c in password
        ):
            return password


# --- Role defaults ---------------------------------------------------------------------


def role_default_row(role: str) -> RoleDefaultPassword:
    row, _ = RoleDefaultPassword.objects.get_or_create(role=role, defaults={"password": generate_password()})
    return row


def role_default(role: str) -> str:
    return role_default_row(role).password


def set_role_default(role: str, password: str, actor) -> RoleDefaultPassword:
    """Validate with AUTH_PASSWORD_VALIDATORS (raises Django's ValidationError) and save."""
    validate_password(password)
    row = role_default_row(role)
    row.password = password
    row.updated_by = actor
    row.save()
    return row


def highest_role(user) -> str | None:
    forget_areas(user)
    roles = {item.role for item in user_areas(user)}
    return next((role for role in ROLE_ORDER if role in roles), None)


def give_default_password(user: User, role: str) -> None:
    """Set the role's default password; the person must change it if the setting says so."""
    user.set_password(role_default(role))
    user.must_change_password = settings.FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN
    user.password_changed_at = None
    user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])


def mask_email(email: str | None) -> str:
    """ra***@example.org: enough to recognise an address in logs without exposing it."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


# --- Login ------------------------------------------------------------------------------------


def invalid_credentials() -> ApiError:
    return ApiError("invalid_credentials", _("Wrong username or password."))


_DUMMY_HASH: str | None = None


def _waste_a_password_check(password: str) -> None:
    """Take as long as a real check, so timing does not reveal unknown usernames."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = make_password(generate_password())
    check_password(password, _DUMMY_HASH)


def _throttled(retry_after: timedelta) -> Throttled:
    seconds = max(1, int(retry_after.total_seconds()) + 1)
    return Throttled(wait=seconds, detail=_("Too many failed logins. Try again in %(s)d seconds.") % {"s": seconds})


def check_login_throttle(key: str, ip: str | None, now) -> None:
    window = timedelta(minutes=settings.LOGIN_USERNAME_WINDOW_MINUTES)
    since = now - window
    last_success = (
        LoginAttempt.objects.filter(username=key, success=True, created_at__gt=since)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last_success:
        since = last_success
    limit = settings.LOGIN_MAX_FAILURES_PER_USERNAME
    failures = list(
        LoginAttempt.objects.filter(username=key, success=False, created_at__gt=since)
        .order_by("-created_at")
        .values_list("created_at", flat=True)[:limit]
    )
    if len(failures) >= limit:
        raise _throttled(failures[-1] + window - now)

    if ip:
        ip_limit = settings.LOGIN_MAX_FAILURES_PER_IP
        ip_failures = list(
            LoginAttempt.objects.filter(ip_address=ip, success=False, created_at__gt=now - HOUR)
            .order_by("-created_at")
            .values_list("created_at", flat=True)[:ip_limit]
        )
        if len(ip_failures) >= ip_limit:
            raise _throttled(ip_failures[-1] + HOUR - now)


def authenticate_login(identifier: str, password: str, ip: str | None = None, *, require=None) -> User:
    """The official for this username or email and password, or raise.

    `require(user)` may refuse the login (the console allows Super Admins
    only); it runs after the password is checked, so it reveals nothing to
    someone without the password.
    """
    key = normalize_login(identifier)
    now = timezone.now()
    check_login_throttle(key, ip, now)

    user = User.objects.filter(Q(username=key) | Q(email=key)).first()
    if user is None or not user.has_usable_password():
        _waste_a_password_check(password)
        ok = False
    else:
        ok = user.check_password(password)
    LoginAttempt.objects.create(username=key, ip_address=ip, success=ok, created_at=now)
    if not ok:
        raise invalid_credentials()

    if user.is_blocked:
        raise ApiError("account_blocked", _("This account is blocked."), status=status.HTTP_403_FORBIDDEN)
    if not user.role_assignments.exists():
        raise permission_denied(_("This account has no role. Ask your admin to assign one."))
    if require is not None:
        require(user)
    User.objects.filter(pk=user.pk).update(last_login=now)
    user.last_login = now
    return user

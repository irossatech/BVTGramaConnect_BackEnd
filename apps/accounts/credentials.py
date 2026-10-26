"""Passwords for officials: login checks, temporary passwords, validation.

- Login (app and console) goes through Django's authenticate(): django-axes
  first refuses locked-out username + IP pairs, then OfficialBackend checks
  the password. Usernames are matched without case. Unknown usernames take as
  long as real ones (ModelBackend hashes a dummy password).
- New accounts and resets get a temporary password, set by the admin or
  generated here and shown once in the API response. It is never emailed,
  logged or audited. must_change_password then forces a change at first login.
"""

from __future__ import annotations

import secrets

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.audit.services import record
from grama.exceptions import ApiError
from grama.http import client_ip

from . import lockout
from .models import User, normalize_login

#: No 0/O, 1/l/I: the password is read off a screen and typed on a phone.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: On the first screen of a phone's symbol keyboard; no quotes or backslash, easy to read aloud.
PASSWORD_SPECIALS = "@#$%&*!?"


def generate_password(length: int = 12) -> str:
    """A temporary password that passes every validator: capital, small letter, number, one special."""
    while True:
        chars = [secrets.choice(PASSWORD_ALPHABET) for _ in range(length - 1)]
        chars.insert(secrets.randbelow(length), secrets.choice(PASSWORD_SPECIALS))
        password = "".join(chars)
        if any(c.isdigit() for c in password) and any(c.islower() for c in password) and any(
            c.isupper() for c in password
        ):
            return password


def check_new_password(password: str, user: User | None = None, field: str = "password") -> None:
    """Raise a validation_error ApiError unless AUTH_PASSWORD_VALIDATORS accept the password."""
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise ApiError("validation_error", exc.messages[0], fields={field: list(exc.messages)}) from None


def set_temporary_password(user: User, password: str | None) -> str | None:
    """Give the user a temporary password they must change at next login.

    With password=None one is generated and returned, for the response only.
    The caller has already validated a chosen password. Saves the user and
    clears any lockout, so the person can log in with it at once.
    """
    generated = None
    if not password:
        password = generated = generate_password()
    user.set_password(password)
    user.must_change_password = True
    user.password_changed_at = None
    user.save(update_fields=["password", "must_change_password", "password_changed_at", "updated_at"])
    lockout.unlock(user)
    return generated


# --- Login ------------------------------------------------------------------------------------


def invalid_credentials() -> ApiError:
    return ApiError("invalid_credentials", _("Wrong username or password."))


def locked_out(request, username: str) -> ApiError:
    seconds = lockout.seconds_left(username, client_ip(request))
    return ApiError(
        "locked_out",
        _("Too many failed logins. Try again in %(m)d minute(s).") % {"m": max(1, (seconds + 59) // 60)},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after_s=seconds,
    )


def refuse(user: User, code: str, message, via: str) -> ApiError:
    record("auth.login_refused", target=user, target_label=user.name, via=via, data={"reason": code})
    return ApiError(code, message, status=status.HTTP_403_FORBIDDEN)


def authenticate_official(request, username: str, password: str, *, via: str, require=None) -> User:
    """The official for this username and password, or raise the contract error.

    `request` is Django's HttpRequest (DRF: request._request). `require(user)`
    may refuse the login (the console needs access_web_console); it runs after
    the password check, so it tells nothing to someone without the password.
    """
    key = normalize_login(username)
    user = authenticate(request, username=key, password=password)
    if user is None:
        if getattr(request, "axes_locked_out", False):
            raise locked_out(request, key)
        raise invalid_credentials()
    if not user.is_active:
        raise refuse(user, "account_blocked", _("This account is blocked."), via)
    if not user.role_assignments.filter(is_active=True).exists():
        raise refuse(user, "permission_denied", _("This account has no role. Ask your admin to assign one."), via)
    if require is not None:
        require(user)
    user.last_login = timezone.now()
    return user

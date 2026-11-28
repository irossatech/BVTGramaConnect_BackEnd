"""django-axes glue: failed-login lockout per username and IP address.

AXES_* settings (grama/settings.py): 5 failures for the same username
from the same IP lock that pair for 15 minutes. Failures during a lockout do
not extend it. A successful login clears the count. Admins with
accounts.unlock_user clear it early (POST /api/v1/users/{id}/unlock/), and a
password reset clears it too.

Failed logins and lockouts are written to the audit log here, for the API and
for Django admin alike. The audit entry never holds the password; the IP
address stays in axes' own tables only.

axes' tables never hold the typed username either: AXES_USERNAME_CALLABLE gives
login_key(), a keyed hash (HMAC-SHA256 with SECRET_KEY) of the username as login
compares it. What was typed may be a password typed into the wrong box, and
AccessFailureLog keeps it for HOUSEKEEPING_KEEP_DAYS. Every lookup by username
goes through login_key(). Changing SECRET_KEY only clears current lockouts.

Cap per address against password spraying (one password tried on many usernames; removed on
2026-11-20, put back before go-live by BVTGC-105, off by default): LOGIN_IP_MAX_FAILURES failed logins
from one IP within LOGIN_IP_WINDOW_MINUTES (60), for any usernames, block every app and console login
from that IP until the oldest of them leaves the window. 0, the default, turns it off. It counts axes' AccessFailureLog rows, by the address
from grama.http.client_ip (which believes X-Real-IP only from the server's own network, BE-SEC-101).
A successful login does not reset it, so an attacker's own account cannot clear it, and an admin's
unlock does not either: it is about an address, not a user.

Ceiling per username (WEB-SEC-019, BVTGC-110, lead's decision of 2026-11-25): guesses spread over
many addresses never reach the pair limit, so LOGIN_MAX_FAILURES_PER_USERNAME (20) failures for one
username within LOGIN_USERNAME_WINDOW_MINUTES (60), from any addresses, lock that username from every
address for LOGIN_LOCKOUT_MINUTES (15) after the last of them. They are counted in axes'
AccessFailureLog, which holds only real tries (axes does not log tries it refused during a lockout),
and tries refused by the ceiling are not counted either, so a lockout is never extended. A
stranger can therefore keep an official out for 15 minutes at a time, never longer without trying
again; an admin's unlock, a password reset or a successful login clears the count.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import get_cool_off, get_failure_limit
from axes.models import AccessAttempt, AccessFailureLog
from axes.signals import user_locked_out
from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db.models import Count, Max
from django.dispatch import receiver
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.masking import mask_text
from grama.http import client_ip

from .models import User, normalize_username

#: Session key holding the user's token_version at login (authentication.py).
SESSION_TOKEN_VERSION = "gc_tv"


def typed_username(request, credentials) -> str:
    """The username as login compares it (normalize_username). For the audit log only, never axes."""
    value = (credentials or {}).get("username")
    if value is None and request is not None:
        value = request.POST.get("username")
    return normalize_username(value)[:150]


def login_key(username) -> str:
    """What axes stores for a username: a keyed hash, never the typed text. "" stays ""."""
    value = normalize_username(username)[:150]
    if not value:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), b"axes-username:" + value.encode(), hashlib.sha256).hexdigest()


def axes_username(request, credentials) -> str:
    """AXES_USERNAME_CALLABLE: the keyed hash of the typed username."""
    return login_key(typed_username(request, credentials))


def axes_client_ip(request) -> str | None:
    """AXES_CLIENT_IP_CALLABLE: the same IP the rest of the app uses (X-Real-IP behind Nginx)."""
    return client_ip(request)


def _cool_off() -> timedelta:
    return get_cool_off() or timedelta(minutes=15)


def seconds_left(username: str, ip: str | None) -> int:
    """How long the username stays locked for this IP, by the pair lockout or the ceiling per
    username, whichever ends later; in whole seconds (at least 1)."""
    latest = (
        AccessAttempt.objects.filter(username=login_key(username), ip_address=ip)
        .order_by("-attempt_time")
        .values_list("attempt_time", flat=True)
        .first()
    )
    pair = 0
    if latest is not None:
        pair = int((latest + _cool_off() - timezone.now()).total_seconds()) + 1
    return max(1, pair, username_seconds_left(username))


def ip_seconds_left(ip: str | None, now=None) -> int:
    """0 while this IP is under the cap per address; else how long it stays blocked, in whole seconds (at least 1)."""
    limit = settings.LOGIN_IP_MAX_FAILURES
    if limit <= 0 or not ip:
        return 0
    now = now or timezone.now()
    window = timedelta(minutes=settings.LOGIN_IP_WINDOW_MINUTES)
    times = list(
        AccessFailureLog.objects.filter(ip_address=ip, attempt_time__gt=now - window)
        .order_by("-attempt_time")
        .values_list("attempt_time", flat=True)[:limit]
    )
    if len(times) < limit:
        return 0
    # Blocked until the oldest of the newest `limit` failures leaves the window.
    return max(1, int((times[-1] + window - now).total_seconds()) + 1)


def _username_locks(keys) -> dict:
    """{login_key: when its ceiling lockout ends} for the keys over the ceiling per username now."""
    limit = settings.LOGIN_MAX_FAILURES_PER_USERNAME
    keys = [key for key in keys if key]
    if limit <= 0 or not keys:
        return {}
    now = timezone.now()
    rows = (
        AccessFailureLog.objects.filter(
            username__in=keys, attempt_time__gte=now - timedelta(minutes=settings.LOGIN_USERNAME_WINDOW_MINUTES)
        )
        .order_by()
        .values("username")
        .annotate(failures=Count("id"), last=Max("attempt_time"))
        .filter(failures__gte=limit, last__gt=now - _cool_off())
    )
    return {row["username"]: row["last"] + _cool_off() for row in rows}


def username_seconds_left(username: str) -> int:
    """How long the ceiling per username keeps this username locked from every address; 0 when it does not."""
    key = login_key(username)
    until = _username_locks([key]).get(key)
    if until is None:
        return 0
    return max(1, int((until - timezone.now()).total_seconds()) + 1)


def locked_usernames(usernames) -> set[str]:
    """Which of these usernames are locked from at least one IP address right now."""
    since = timezone.now() - _cool_off()
    limit = get_failure_limit(None, None)
    by_key = {login_key(name): name for name in usernames}
    locked = set(
        AccessAttempt.objects.filter(
            username__in=list(by_key), attempt_time__gte=since, failures_since_start__gte=limit
        ).values_list("username", flat=True)
    )
    locked |= set(_username_locks(list(by_key)))
    return {by_key[key] for key in locked}


def _clear_username_failures(username: str) -> int:
    key = login_key(username)
    if not key:
        return 0
    deleted, _rows = AccessFailureLog.objects.filter(username=key).delete()
    return deleted


def unlock(user: User) -> int:
    """Clear every failed attempt for this user's username, from any IP, and the count of the
    ceiling per username. Returns the lockout rows removed (AccessAttempt; the failure log not counted)."""
    _clear_username_failures(user.username)
    return AxesProxyHandler.reset_attempts(username=login_key(user.username))


def audit_username_lockout(request, username: str) -> None:
    """auth.locked_out for the ceiling per username, once: on the failure that reached it."""
    from apps.audit.services import record

    target = User.objects.filter(username=username).first() if username else None
    shown = _username_for_audit(username, target)
    record(
        "auth.locked_out",
        target=target,
        target_label=shown,
        via=_via(request),
        data={"username": shown, "minutes": int(_cool_off().total_seconds() // 60), "scope": "username"},
    )


def audit_refused_try(request, username: str) -> None:
    """auth.login_failed (locked) for a try the ceiling refused before the password was checked."""
    _audit_failure(request, username, locked=True)


def _via(request) -> str:
    path = getattr(request, "path", "") or ""
    if path.startswith("/django-admin/"):
        return "admin"
    if "/session/" in path:
        return "console"
    return "app"


@receiver(user_login_failed, dispatch_uid="audit_failed_login")
def audit_failed_login(sender, credentials=None, request=None, **kwargs):
    _audit_failure(request, typed_username(request, credentials), locked=bool(getattr(request, "axes_locked_out", False)))


def _audit_failure(request, username: str, *, locked: bool) -> None:
    from apps.audit.services import record

    target = User.objects.filter(username=username).first() if username else None
    shown = _username_for_audit(username, target)
    record(
        "auth.login_failed",
        target=target,
        target_label=shown,
        via=_via(request),
        data={"username": shown, "locked": locked},
    )


def _username_for_audit(username: str, target) -> str:
    """A real account's username as is; anything else masked.

    What was typed for an unknown account may be a password typed into the wrong box, or an
    email address, so the audit log keeps only its first character.
    """
    return username if target is not None else mask_text(username)


@receiver(user_locked_out, dispatch_uid="audit_lockout")
def audit_lockout(sender, request=None, username=None, ip_address=None, **kwargs):
    """axes sends this on the failure that locks, and again on tries during the lockout; audit the first.

    The signal's `username` is the hash (login_key); the typed name comes from the credentials
    axes keeps on the request just before it sends the signal.
    """
    if getattr(request, "axes_failures_since_start", None) is None:
        return
    from apps.audit.services import record

    username = typed_username(request, getattr(request, "axes_credentials", None))
    target = User.objects.filter(username=username).first() if username else None
    shown = _username_for_audit(username, target)
    record(
        "auth.locked_out",
        target=target,
        target_label=shown,
        via=_via(request),
        data={"username": shown, "minutes": int(_cool_off().total_seconds() // 60)},
    )


@receiver(user_logged_in, dispatch_uid="remember_token_version")
def remember_token_version(sender, request=None, user=None, **kwargs):
    """Session logins (console, Django admin) store token_version, so a block or reset ends them."""
    session = getattr(request, "session", None)
    if session is not None and user is not None and "_auth_user_id" in session:
        session[SESSION_TOKEN_VERSION] = user.token_version


@receiver(user_logged_in, dispatch_uid="clear_username_failures")
def clear_username_failures(sender, request=None, user=None, **kwargs):
    """A successful login clears the count of the ceiling per username, as axes clears the pair's."""
    if user is not None:
        _clear_username_failures(user.username)


def lockout_response(request, original_response=None, credentials=None):
    """AXES_LOCKOUT_CALLABLE, used by AxesMiddleware.

    The API login views already answered 429 locked_out in the contract's
    shape; keep that. Other login forms (Django admin) get a plain 429.
    """
    if original_response is not None and getattr(original_response, "status_code", None) == 429:
        return original_response
    if (getattr(request, "path", "") or "").startswith("/api/"):
        # The same answer as credentials.locked_out (BVTGC-79): the time left for this username and
        # address, in the message (the request's language) and as retry_after_s for the countdown.
        # Without a username to look up, the whole lockout time.
        username = typed_username(request, credentials)
        seconds = seconds_left(username, client_ip(request)) if username else int(_cool_off().total_seconds())
        message = _("Too many failed logins. Try again in %(m)d minute(s).") % {"m": max(1, (seconds + 59) // 60)}
        return JsonResponse(
            {"error": {"code": "locked_out", "message": message, "retry_after_s": seconds}},
            status=429,
            json_dumps_params={"ensure_ascii": False},
        )
    return HttpResponse("Too many failed logins. Try again in 15 minutes.", status=429, content_type="text/plain")

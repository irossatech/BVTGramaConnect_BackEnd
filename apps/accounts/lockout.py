"""django-axes glue: failed-login lockout per username and IP address.

AXES_* settings (grama/settings/base.py): 5 failures for the same username
from the same IP lock that pair for 15 minutes. Failures during a lockout do
not extend it. A successful login clears the count. Admins with
accounts.unlock_user clear it early (POST /api/v1/users/{id}/unlock/), and a
password reset clears it too.

Failed logins and lockouts are written to the audit log here, for the API and
for Django admin alike. The audit entry never holds the password; the IP
address stays in axes' own table only.
"""

from __future__ import annotations

from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import get_cool_off, get_failure_limit
from axes.models import AccessAttempt
from axes.signals import user_locked_out
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from grama.http import client_ip

from .models import User, normalize_login

#: Session key holding the user's token_version at login (authentication.py).
SESSION_TOKEN_VERSION = "gc_tv"


def axes_username(request, credentials) -> str:
    """AXES_USERNAME_CALLABLE: the username as login compares it (trimmed, lower case)."""
    value = (credentials or {}).get("username")
    if value is None and request is not None:
        value = request.POST.get("username")
    return normalize_login(value)[:150]


def axes_client_ip(request) -> str | None:
    """AXES_CLIENT_IP_CALLABLE: the same IP the rest of the app uses (X-Real-IP behind Nginx)."""
    return client_ip(request)


def _cool_off() -> timedelta:
    return get_cool_off() or timedelta(minutes=15)


def seconds_left(username: str, ip: str | None) -> int:
    """How long the (username, IP) pair stays locked, in whole seconds (at least 1)."""
    latest = (
        AccessAttempt.objects.filter(username=username, ip_address=ip)
        .order_by("-attempt_time")
        .values_list("attempt_time", flat=True)
        .first()
    )
    if latest is None:
        return 1
    remaining = latest + _cool_off() - timezone.now()
    return max(1, int(remaining.total_seconds()) + 1)


def locked_usernames(usernames) -> set[str]:
    """Which of these usernames are locked from at least one IP address right now."""
    since = timezone.now() - _cool_off()
    limit = get_failure_limit(None, None)
    return set(
        AccessAttempt.objects.filter(
            username__in=list(usernames), attempt_time__gte=since, failures_since_start__gte=limit
        ).values_list("username", flat=True)
    )


def unlock(user: User) -> int:
    """Clear every failed attempt for this user's username, from any IP. Returns the rows removed."""
    return AxesProxyHandler.reset_attempts(username=user.username)


def _via(request) -> str:
    path = getattr(request, "path", "") or ""
    if path.startswith("/django-admin/"):
        return "admin"
    if "/session/" in path:
        return "console"
    return "app"


@receiver(user_login_failed, dispatch_uid="audit_failed_login")
def audit_failed_login(sender, credentials=None, request=None, **kwargs):
    from apps.audit.services import record

    username = axes_username(request, credentials)
    target = User.objects.filter(username=username).first() if username else None
    record(
        "auth.login_failed",
        target=target,
        target_label=username,
        via=_via(request),
        data={"username": username, "locked": bool(getattr(request, "axes_locked_out", False))},
    )


@receiver(user_locked_out, dispatch_uid="audit_lockout")
def audit_lockout(sender, request=None, username=None, ip_address=None, **kwargs):
    """axes sends this on the failure that locks, and again on tries during the lockout; audit the first."""
    if getattr(request, "axes_failures_since_start", None) is None:
        return
    from apps.audit.services import record

    username = normalize_login(username)
    target = User.objects.filter(username=username).first() if username else None
    record(
        "auth.locked_out",
        target=target,
        target_label=username,
        via=_via(request),
        data={"username": username, "minutes": int(_cool_off().total_seconds() // 60)},
    )


@receiver(user_logged_in, dispatch_uid="remember_token_version")
def remember_token_version(sender, request=None, user=None, **kwargs):
    """Session logins (console, Django admin) store token_version, so a block or reset ends them."""
    session = getattr(request, "session", None)
    if session is not None and user is not None and "_auth_user_id" in session:
        session[SESSION_TOKEN_VERSION] = user.token_version


def lockout_response(request, original_response=None, credentials=None):
    """AXES_LOCKOUT_CALLABLE, used by AxesMiddleware.

    The API login views already answered 429 locked_out in the contract's
    shape; keep that. Other login forms (Django admin) get a plain 429.
    """
    if original_response is not None and getattr(original_response, "status_code", None) == 429:
        return original_response
    if (getattr(request, "path", "") or "").startswith("/api/"):
        return JsonResponse(
            {"error": {"code": "locked_out", "message": "Too many failed logins. Try again later."}}, status=429
        )
    return HttpResponse("Too many failed logins. Try again in 15 minutes.", status=429, content_type="text/plain")

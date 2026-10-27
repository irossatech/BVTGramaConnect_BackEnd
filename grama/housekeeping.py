"""Daily cleanup of rows that would otherwise grow forever. Run by django-q2.

- Failed django-q tasks older than HOUSEKEEPING_KEEP_DAYS. django-q2 already
  keeps only the newest Q_CLUSTER["save_limit"] (250) successful tasks itself.
- Expired JWT refresh tokens (simplejwt's OutstandingToken; their
  BlacklistedToken rows go with them), as `flushexpiredtokens` does.
- Expired console sessions, as `clearsessions` does.
- django-axes AccessFailureLog rows (one per failed login, with the IP) older
  than the IP-wide cap's window (LOGIN_IP_WINDOW_MINUTES): nothing reads them
  after that, and IP addresses are not kept longer than needed.

django-axes AccessAttempt rows need nothing here: axes deletes attempts older
than the lockout on every login, failed login and logout. The audit log is
never cleaned; it is the permanent record.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

HOUSEKEEPING_SCHEDULE = "daily-housekeeping"


def run_housekeeping(now=None) -> dict:
    """Delete the rows above. Safe to run any number of times. Returns the rows removed per kind."""
    from axes.models import AccessFailureLog
    from django.contrib.sessions.models import Session
    from django_q.models import Failure
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.HOUSEKEEPING_KEEP_DAYS)
    failed_tasks, _rows = Failure.objects.filter(stopped__lt=cutoff).delete()
    tokens = OutstandingToken.objects.filter(expires_at__lte=now)
    expired_tokens = tokens.count()
    tokens.delete()
    sessions, _rows = Session.objects.filter(expire_date__lt=now).delete()
    login_failures, _rows = AccessFailureLog.objects.filter(
        attempt_time__lte=now - timedelta(minutes=settings.LOGIN_IP_WINDOW_MINUTES)
    ).delete()
    return {
        "failed_tasks": failed_tasks,
        "expired_tokens": expired_tokens,
        "expired_sessions": sessions,
        "login_failures": login_failures,
    }


def ensure_housekeeping_schedule() -> None:
    from django_q.models import Schedule

    Schedule.objects.update_or_create(
        name=HOUSEKEEPING_SCHEDULE,
        defaults={"func": "grama.housekeeping.run_housekeeping", "schedule_type": Schedule.DAILY, "repeats": -1},
    )

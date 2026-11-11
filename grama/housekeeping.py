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
#: Rows removed per DELETE: each batch is a short statement of its own, never one long delete holding
#: locks on a big table (audit finding BE-DB-019).
DELETE_BATCH = 1000


def _delete_in_batches(queryset) -> int:
    """Delete every row of `queryset`, DELETE_BATCH at a time. Returns how many rows of its model went
    (rows removed with them, such as a token's blacklist entry, are not counted)."""
    model = queryset.model
    removed = 0
    while True:
        batch = list(queryset.order_by("pk").values_list("pk", flat=True)[:DELETE_BATCH])
        if not batch:
            return removed
        model.objects.filter(pk__in=batch).delete()
        removed += len(batch)


def run_housekeeping(now=None) -> dict:
    """Delete the rows above. Safe to run any number of times. Returns the rows removed per kind."""
    from axes.models import AccessFailureLog
    from django.contrib.sessions.models import Session
    from django_q.models import Failure
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.HOUSEKEEPING_KEEP_DAYS)
    failed_tasks = _delete_in_batches(Failure.objects.filter(stopped__lt=cutoff))
    expired_tokens = _delete_in_batches(OutstandingToken.objects.filter(expires_at__lte=now))
    sessions = _delete_in_batches(Session.objects.filter(expire_date__lt=now))
    login_failures = _delete_in_batches(
        AccessFailureLog.objects.filter(attempt_time__lte=now - timedelta(minutes=settings.LOGIN_IP_WINDOW_MINUTES))
    )
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

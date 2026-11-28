"""Daily cleanup of rows that would otherwise grow forever. Run by django-q2.

- Failed django-q tasks older than HOUSEKEEPING_KEEP_DAYS. django-q2 already
  keeps only the newest Q_CLUSTER["save_limit"] (250) successful tasks itself.
- Expired JWT refresh tokens (simplejwt's OutstandingToken; their
  BlacklistedToken rows go with them), as `flushexpiredtokens` does.
- Refresh rotations (which refresh token replaced which, token IDs only) older
  than a day; the grace for a lost refresh answer needs only seconds (BVTGC-93).
- Expired console sessions, as `clearsessions` does.
- django-axes AccessFailureLog rows (one per failed login, with the IP) older
  than LOGIN_FAILURE_LOG_KEEP_MINUTES (60): IP addresses are not kept longer
  than needed. The ceiling per username reads them (apps/accounts/lockout.py),
  so a longer LOGIN_USERNAME_WINDOW_MINUTES keeps them that long instead.
- Officials' read notifications older than NOTIFICATION_KEEP_DAYS (90; 0 keeps
  them all). Unread ones stay until read (owner decision, audit finding BE-DB-018).
- Citizen wallet accounts (BVTGC-127, apps.citizens.services_account.housekeeping):
  unfinished sign-ups past 24 hours, email codes older than a week, app-use days
  older than 60 days, and deleted accounts' fingerprints after CITIZEN_DELETED_KEEP_DAYS.
  None of these are records: an unfinished form, a code, a counter, a fingerprint.
- Not a deletion: the files of taken-down and discarded posts are moved out of the public
  media folder, and those of posts shown again back in (apps.posts.hidden_media.reconcile,
  BVTGC-192). Nothing is deleted.

Push devices, citizen phones, contact messages, posts, service requests and the
audit log are never deleted here (owner decision).

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
#: How long a failed login's AccessFailureLog row (with its IP) is kept.
LOGIN_FAILURE_LOG_KEEP_MINUTES = 60


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

    from apps.accounts.models import RefreshRotation

    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.HOUSEKEEPING_KEEP_DAYS)
    failed_tasks = _delete_in_batches(Failure.objects.filter(stopped__lt=cutoff))
    expired_tokens = _delete_in_batches(OutstandingToken.objects.filter(expires_at__lte=now))
    # Which refresh token replaced which: needed only for JWT_REFRESH_GRACE_SECONDS (BVTGC-93).
    _delete_in_batches(RefreshRotation.objects.filter(at__lt=now - timedelta(days=1)))
    sessions = _delete_in_batches(Session.objects.filter(expire_date__lt=now))
    keep = max(LOGIN_FAILURE_LOG_KEEP_MINUTES, settings.LOGIN_USERNAME_WINDOW_MINUTES, settings.LOGIN_IP_WINDOW_MINUTES)
    login_failures = _delete_in_batches(AccessFailureLog.objects.filter(attempt_time__lte=now - timedelta(minutes=keep)))
    return {
        "failed_tasks": failed_tasks,
        "expired_tokens": expired_tokens,
        "expired_sessions": sessions,
        "login_failures": login_failures,
        "read_notifications": _delete_old_read_notifications(now),
        **_citizen_wallet(now),
        "hidden_media_moved": _hidden_media(),
    }


def _hidden_media() -> int:
    """Files of taken-down and discarded posts out of the public folder, shown ones back in (BVTGC-192):
    the first run after the deploy hides those of posts hidden before; later runs finish a cut-short move."""
    from apps.posts.hidden_media import reconcile

    return reconcile()


def _citizen_wallet(now) -> dict:
    from apps.citizens.services_account import housekeeping

    return housekeeping(now)


def _delete_old_read_notifications(now) -> int:
    from apps.notifications.models import Notification

    if settings.NOTIFICATION_KEEP_DAYS <= 0:
        return 0
    cutoff = now - timedelta(days=settings.NOTIFICATION_KEEP_DAYS)
    return _delete_in_batches(Notification.objects.filter(created_at__lt=cutoff, read_at__isnull=False))


def ensure_housekeeping_schedule() -> None:
    from django_q.models import Schedule

    from grama.schedules import ensure_schedule

    ensure_schedule(
        HOUSEKEEPING_SCHEDULE, func="grama.housekeeping.run_housekeeping", schedule_type=Schedule.DAILY, repeats=-1
    )

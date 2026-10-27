"""Daily housekeeping job (grama/housekeeping.py) and its schedule."""

import io
import uuid
from datetime import timedelta

import pytest
from axes.models import AccessFailureLog
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.utils import timezone
from django.utils.module_loading import import_string
from django_q.models import Schedule, Task
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from grama.housekeeping import run_housekeeping

pytestmark = pytest.mark.django_db

KEEP_DAYS = 30  # HOUSEKEEPING_KEEP_DAYS default


def task(days_ago, *, success):
    at = timezone.now() - timedelta(days=days_ago)
    return Task.objects.create(
        id=uuid.uuid4().hex, name=f"t-{uuid.uuid4().hex[:8]}", func="apps.x.y", started=at, stopped=at, success=success
    )


def token(user, *, expires_in_days):
    return OutstandingToken.objects.create(
        user=user, jti=uuid.uuid4().hex, token="t", expires_at=timezone.now() + timedelta(days=expires_in_days)
    )


def session(*, expires_in_minutes):
    return Session.objects.create(
        session_key=uuid.uuid4().hex, session_data="x", expire_date=timezone.now() + timedelta(minutes=expires_in_minutes)
    )


def login_failure(*, minutes_ago):
    row = AccessFailureLog.objects.create(username="x", ip_address="10.1.1.1", user_agent="", http_accept="", path_info="")
    AccessFailureLog.objects.filter(pk=row.pk).update(attempt_time=timezone.now() - timedelta(minutes=minutes_ago))
    return row


def test_old_failed_tasks_go_recent_ones_and_successes_stay():
    old_failure = task(KEEP_DAYS + 1, success=False)
    recent_failure = task(KEEP_DAYS - 1, success=False)
    old_success = task(KEEP_DAYS + 10, success=True)  # django-q2's own save_limit handles these

    assert run_housekeeping()["failed_tasks"] == 1
    assert set(Task.objects.values_list("id", flat=True)) == {recent_failure.id, old_success.id}
    assert not Task.objects.filter(id=old_failure.id).exists()


def test_expired_tokens_go_with_their_blacklist_rows_valid_ones_stay(world):
    user = world.users["pm_kaza"]
    expired = token(user, expires_in_days=-1)
    BlacklistedToken.objects.create(token=expired)
    valid = token(user, expires_in_days=29)
    BlacklistedToken.objects.create(token=valid)  # a logged-out token stays blacklisted until it expires

    assert run_housekeeping()["expired_tokens"] == 1
    assert list(OutstandingToken.objects.values_list("id", flat=True)) == [valid.id]
    assert list(BlacklistedToken.objects.values_list("token_id", flat=True)) == [valid.id]


def test_expired_sessions_go_live_ones_stay():
    session(expires_in_minutes=-1)
    live = session(expires_in_minutes=30)

    assert run_housekeeping()["expired_sessions"] == 1
    assert list(Session.objects.values_list("session_key", flat=True)) == [live.session_key]


def test_login_failures_older_than_the_ip_window_go(settings):
    settings.LOGIN_IP_WINDOW_MINUTES = 60
    login_failure(minutes_ago=61)
    recent = login_failure(minutes_ago=30)

    assert run_housekeeping()["login_failures"] == 1
    assert list(AccessFailureLog.objects.values_list("id", flat=True)) == [recent.id]


def test_running_twice_is_safe(world):
    task(KEEP_DAYS + 1, success=False)
    token(world.users["pm_kaza"], expires_in_days=-1)
    session(expires_in_minutes=-1)

    login_failure(minutes_ago=61)

    assert run_housekeeping() == {"failed_tasks": 1, "expired_tokens": 1, "expired_sessions": 1, "login_failures": 1}
    assert run_housekeeping() == {"failed_tasks": 0, "expired_tokens": 0, "expired_sessions": 0, "login_failures": 0}


def test_keep_days_setting(settings):
    settings.HOUSEKEEPING_KEEP_DAYS = 3
    task(4, success=False)
    task(2, success=False)
    assert run_housekeeping()["failed_tasks"] == 1


def test_ensure_schedules_registers_the_daily_job():
    call_command("ensure_schedules", stdout=io.StringIO())
    call_command("ensure_schedules", stdout=io.StringIO())
    schedule = Schedule.objects.get(name="daily-housekeeping")
    assert schedule.func == "grama.housekeeping.run_housekeeping"
    assert schedule.schedule_type == Schedule.DAILY
    assert schedule.repeats == -1
    assert import_string(schedule.func) is run_housekeeping
    assert Schedule.objects.count() == 2  # escalation and housekeeping, no duplicates

"""The ceiling per username across addresses (WEB-SEC-019, BVTGC-110, lead's decision of 2026-11-25).

20 failed logins for one username within an hour, from any addresses, lock that username from every
address for 15 minutes after the last of them. The pair lockout (5 from one address) stays as it was.
"""

from datetime import timedelta

import pytest
from axes.models import AccessFailureLog
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD
from grama.housekeeping import run_housekeeping

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CSRF = "/api/v1/auth/csrf/"
SESSION_LOGIN = "/api/v1/auth/session/login/"
CEILING = 20


def login(username, password=TEST_PASSWORD, ip="10.30.0.1"):
    return APIClient(REMOTE_ADDR=ip).post(LOGIN, {"username": username, "password": password}, format="json")


def fail_from_many(username, n, first=1):
    """n wrong passwords, each from its own address (never 5 from one), so no pair lockout."""
    return [login(username, "wrong", ip=f"10.20.{(first + i) // 250}.{(first + i) % 250 + 1}") for i in range(n)]


def code(response):
    return response.json()["error"]["code"]


def age_failures(minutes):
    AccessFailureLog.objects.update(attempt_time=timezone.now() - timedelta(minutes=minutes))


def test_the_twentieth_failure_from_many_addresses_locks_the_username_everywhere(world):
    answers = fail_from_many("va_kolakaluru", CEILING)
    assert all(code(answer) == "invalid_credentials" for answer in answers[:-1])
    assert answers[-1].status_code == 429 and code(answers[-1]) == "locked_out"
    locked = login("va_kolakaluru", ip="10.99.99.99")  # a new address, the right password
    assert locked.status_code == 429
    error = locked.json()["error"]
    assert error["code"] == "locked_out" and 800 < error["retry_after_s"] <= 901


def test_nineteen_failures_from_many_addresses_do_not_lock(world):
    fail_from_many("va_kolakaluru", CEILING - 1)
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200


def test_the_console_login_is_refused_too(world):
    fail_from_many("super", CEILING)
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR="10.99.99.98")
    client.get(CSRF)
    response = client.post(
        SESSION_LOGIN, {"username": "super", "password": TEST_PASSWORD}, format="json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == 429 and code(response) == "locked_out"


def test_the_lockout_ends_fifteen_minutes_after_the_last_failure(world):
    fail_from_many("va_kolakaluru", CEILING)
    age_failures(14)
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 429
    age_failures(16)
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200


def test_tries_during_the_lockout_are_not_counted_and_do_not_extend_it(world):
    fail_from_many("va_kolakaluru", CEILING)
    for index in range(5):
        assert login("va_kolakaluru", "wrong", ip=f"10.40.0.{index + 1}").status_code == 429
    assert AccessFailureLog.objects.count() == CEILING
    refused = AuditLog.objects.filter(action="auth.login_failed", data__locked=True)
    assert refused.count() == 5


def test_failures_older_than_the_window_do_not_count(world):
    fail_from_many("va_kolakaluru", CEILING - 1)
    age_failures(61)
    assert code(fail_from_many("va_kolakaluru", 1, first=100)[0]) == "invalid_credentials"
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200


def test_a_successful_login_clears_the_count(world):
    fail_from_many("va_kolakaluru", CEILING - 1)
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200
    answers = fail_from_many("va_kolakaluru", CEILING - 1, first=100)
    assert all(code(answer) == "invalid_credentials" for answer in answers)


def test_other_usernames_are_not_locked(world):
    fail_from_many("va_kolakaluru", CEILING)
    assert login("ma_tenali", ip="10.99.99.99").status_code == 200


def test_the_lockout_is_audited_once(world):
    fail_from_many("va_kolakaluru", CEILING)
    login("va_kolakaluru", ip="10.99.99.99")
    entry = AuditLog.objects.get(action="auth.locked_out")
    assert entry.target_id == str(world.users["va_kolakaluru"].id)
    assert entry.data == {"username": "va_kolakaluru", "minutes": 15, "scope": "username"}


def test_an_unknown_username_is_locked_and_masked_in_the_audit_log(world):
    fail_from_many("nobody-here", CEILING)
    entry = AuditLog.objects.get(action="auth.locked_out")
    assert entry.target_id == "" and entry.data["username"] != "nobody-here"
    assert "nobody-here" not in str(entry.data) and entry.data["scope"] == "username"


def test_an_admin_ranked_above_unlocks_early(api, world):
    fail_from_many("va_kolakaluru", CEILING)
    target = world.users["va_kolakaluru"]
    client = api(world.users["super"])
    row = client.get(f"/api/v1/users/{target.id}/").json()
    assert row["locked_out"] is True and "unlock" in row["allowed_actions"]
    response = client.post(f"/api/v1/users/{target.id}/unlock/", {}, format="json")
    assert response.status_code == 200 and response.json()["locked_out"] is False
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200
    assert AuditLog.objects.filter(action="user.unlocked").count() == 1


def test_a_password_reset_clears_it(api, world):
    fail_from_many("va_kolakaluru", CEILING)
    target = world.users["va_kolakaluru"]
    response = api(world.users["super"]).post(
        f"/api/v1/users/{target.id}/reset-password/", {"password": "Kolakaluru-Reset-2026"}, format="json"
    )
    assert response.status_code == 200
    assert login("va_kolakaluru", "Kolakaluru-Reset-2026", ip="10.99.99.99").status_code == 200


def test_zero_turns_the_ceiling_off(world, settings):
    settings.LOGIN_MAX_FAILURES_PER_USERNAME = 0
    answers = fail_from_many("va_kolakaluru", CEILING + 5)
    assert all(code(answer) == "invalid_credentials" for answer in answers)
    assert login("va_kolakaluru", ip="10.99.99.99").status_code == 200


def test_the_numbers_come_from_settings(world, settings):
    settings.LOGIN_MAX_FAILURES_PER_USERNAME = 8
    answers = fail_from_many("va_kolakaluru", 8)
    assert code(answers[-2]) == "invalid_credentials" and code(answers[-1]) == "locked_out"


def test_the_defaults_are_the_leads_numbers():
    from django.conf import settings

    assert settings.LOGIN_MAX_FAILURES_PER_USERNAME == 20
    assert settings.LOGIN_USERNAME_WINDOW_MINUTES == 60
    assert settings.AXES_COOLOFF_TIME == timedelta(minutes=15)


def test_housekeeping_keeps_failures_for_a_longer_window(world, settings):
    settings.LOGIN_USERNAME_WINDOW_MINUTES = 120
    fail_from_many("va_kolakaluru", 2)
    age_failures(90)
    run_housekeeping()
    assert AccessFailureLog.objects.count() == 2
    age_failures(121)
    run_housekeeping()
    assert AccessFailureLog.objects.count() == 0

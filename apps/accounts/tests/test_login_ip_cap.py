"""The cap per address against password spraying (BVTGC-105, put back before go-live).

LOGIN_IP_MAX_FAILURES failed logins from one IP within LOGIN_IP_WINDOW_MINUTES (60), for any
usernames, block every app and console login from that IP until the oldest of them leaves the window.
The cap ships switched off (0) until production confirms real visitor addresses (BVTGC-39).
These tests were removed with the cap on 2026-11-20 (79aaca7) and are back with it.
"""

from datetime import timedelta

import pytest
from axes.models import AccessFailureLog
from django.conf import settings as django_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CSRF = "/api/v1/auth/csrf/"
SESSION_LOGIN = "/api/v1/auth/session/login/"


def login(username, password=TEST_PASSWORD, ip="127.0.0.1"):
    return APIClient(REMOTE_ADDR=ip).post(LOGIN, {"username": username, "password": password}, format="json")


def code(response):
    return response.json()["error"]["code"]


def spray(times, ip="127.0.0.1"):
    """One wrong password on `times` different usernames from one IP: never 5 for one username."""
    return [login(f"nobody{index}", "Gram@2026", ip=ip) for index in range(times)]


def test_failures_on_many_usernames_block_the_ip(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 6
    responses = spray(6)
    assert [code(r) for r in responses] == ["invalid_credentials"] * 5 + ["locked_out"]
    blocked = login("va_kolakaluru")  # even a real official with the right password, from that IP
    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert error["code"] == "locked_out" and 3500 < error["retry_after_s"] <= 3601
    # Another address is not blocked.
    assert login("va_kolakaluru", ip="10.9.9.8").status_code == 200
    # An hour later the address may try again.
    AccessFailureLog.objects.update(attempt_time=timezone.now() - timedelta(minutes=61))
    assert login("va_kolakaluru").status_code == 200


def test_ip_block_is_audited_once_without_the_ip(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 6
    spray(9)
    entry = AuditLog.objects.get(action="auth.ip_locked_out")
    assert entry.data == {"failures": 6, "minutes": 60}
    assert "127.0.0.1" not in str(entry.data) and entry.target_label == ""
    # Tries while blocked never reach the password check: no more failures are logged.
    assert AuditLog.objects.filter(action="auth.login_failed").count() == 6


def test_tries_during_an_ip_block_do_not_extend_it(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 6
    spray(6)
    count = AccessFailureLog.objects.count()
    spray(3)
    login("va_kolakaluru")
    assert AccessFailureLog.objects.count() == count


def test_a_success_does_not_reset_the_ip_count(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 6
    spray(5)
    assert login("va_kolakaluru").status_code == 200  # an attacker's own account cannot clear it
    assert code(login("ma_tenali", "wrong")) == "locked_out"


def test_the_ip_cap_covers_the_console_login(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 6
    spray(6)
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1")
    client.get(CSRF)
    response = client.post(
        SESSION_LOGIN, {"username": "super", "password": TEST_PASSWORD}, format="json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == 429 and code(response) == "locked_out"


def test_ip_cap_zero_switches_it_off(world, settings):
    settings.LOGIN_IP_MAX_FAILURES = 0
    spray(60)
    assert login("va_kolakaluru").status_code == 200


def test_many_good_logins_from_one_office_are_not_limited(world):
    """No login throttle (30 an hour per IP, not decided): one office network, many logins, all get in."""
    for _ in range(40):
        assert login("va_kolakaluru", ip="10.7.7.8").status_code == 200


def test_ip_cap_is_off_by_default():
    assert django_settings.LOGIN_IP_MAX_FAILURES == 0 and django_settings.LOGIN_IP_WINDOW_MINUTES == 60
    assert django_settings.AXES_ENABLE_ACCESS_FAILURE_LOG is True


def test_with_the_default_many_failures_from_one_address_block_no_one_else(world):
    """The shipped default: one shared address (a mobile network) with many wrong passwords on
    many usernames still lets every other user in. The per-username limits stay on."""
    spray(80)
    assert login("va_kolakaluru").status_code == 200
    for _ in range(5):
        login("ma_tenali", "wrong")
    assert code(login("ma_tenali")) == "locked_out"

"""Security gap G1, as the gaps file gives the fix: a throttle of 30 logins an hour per IP address
on the app login and the console login, successful or not, answering 429 throttled."""

import pytest
from django.conf import settings as django_settings
from rest_framework.test import APIClient

from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
SESSION_LOGIN = "/api/v1/auth/session/login/"


def login(username="va_kolakaluru", password=TEST_PASSWORD, ip="10.1.1.1", url=LOGIN):
    client = APIClient(REMOTE_ADDR=ip)
    return client.post(url, {"username": username, "password": password}, format="json")


def code(response):
    return response.json()["error"]["code"]


def test_the_default_is_thirty_an_hour():
    assert django_settings.LOGIN_THROTTLE_RATE == "30/hour"


def test_successful_logins_count_too_and_the_next_one_waits(world, settings):
    settings.LOGIN_THROTTLE_RATE = "3/hour"
    for _ in range(3):
        assert login().status_code == 200
    response = login()
    assert response.status_code == 429 and code(response) == "throttled"
    wait = response.json()["error"]["retry_after_s"]
    assert 0 < wait <= 3600 and int(response["Retry-After"]) == wait


def test_failed_logins_and_other_usernames_share_the_count(world, settings):
    settings.LOGIN_THROTTLE_RATE = "3/hour"
    login(password="wrong")
    login(username="pm_kaza", password="wrong")
    login(username="nobody.here", password="wrong")
    assert code(login()) == "throttled"  # even the right password


def test_the_app_and_the_console_share_one_count(world, settings):
    settings.LOGIN_THROTTLE_RATE = "2/hour"
    login()
    login(url=SESSION_LOGIN, username="super")
    assert code(login(url=SESSION_LOGIN, username="super")) == "throttled"


def test_each_ip_address_has_its_own_count(world, settings):
    settings.LOGIN_THROTTLE_RATE = "1/hour"
    assert login(ip="10.1.1.1").status_code == 200
    assert login(ip="10.1.1.1").status_code == 429
    assert login(ip="10.2.2.2").status_code == 200


def test_other_endpoints_are_not_counted(world, settings, api):
    settings.LOGIN_THROTTLE_RATE = "1/hour"
    client = api(world.users["va_kolakaluru"])
    for _ in range(5):
        assert client.get("/api/v1/me/").status_code == 200
    assert login().status_code == 200

"""The backup locked_out answer (django-axes' lockout callable) says how long to wait (BVTGC-79).

The login views answer 429 locked_out themselves. When axes blocks an /api/ request first, its
callable answers instead; that answer now matches the login's: the time left for this username and
address in the message (the request's language) and as retry_after_s.
"""

import json

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import translation

from apps.accounts.lockout import lockout_response

pytestmark = pytest.mark.django_db
LOGIN = "/api/v1/auth/login/"


@pytest.fixture
def locked(api, world, settings):
    """va_kolakaluru locked from 127.0.0.1 after LOGIN_MAX_FAILURES wrong passwords."""
    client = api()
    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(LOGIN, {"username": "va_kolakaluru", "password": "wrong"}, format="json")
    assert client.post(LOGIN, {"username": "va_kolakaluru", "password": "wrong"}, format="json").status_code == 429
    return "va_kolakaluru"


def backup_answer(credentials):
    request = RequestFactory().post(LOGIN, REMOTE_ADDR="127.0.0.1")
    return lockout_response(request, None, credentials)


def test_it_gives_the_time_left_in_seconds_and_minutes(locked, settings):
    response = backup_answer({"username": locked})
    assert response.status_code == 429
    error = json.loads(response.content)["error"]
    cool_off = int(settings.AXES_COOLOFF_TIME.total_seconds())
    assert error["code"] == "locked_out"
    assert 0 < error["retry_after_s"] <= cool_off + 1
    minutes = max(1, (error["retry_after_s"] + 59) // 60)
    # Telugu by default, raw UTF-8 like every API answer.
    assert error["message"] == f"చాలాసార్లు లాగిన్ విఫలమైంది. {minutes} నిమిషాల తర్వాత మళ్ళీ ప్రయత్నించండి."
    assert "చాలాసార్లు".encode() in response.content


def test_it_follows_the_language(locked):
    with translation.override("en"):
        error = json.loads(backup_answer({"username": locked}).content)["error"]
    minutes = max(1, (error["retry_after_s"] + 59) // 60)
    assert error["message"] == f"Too many failed logins. Try again in {minutes} minute(s)."


def test_without_a_username_it_gives_the_whole_lockout_time(db, settings):
    error = json.loads(backup_answer({}).content)["error"]
    assert error["retry_after_s"] == int(settings.AXES_COOLOFF_TIME.total_seconds())


def test_the_login_views_own_answer_is_kept(db):
    own = HttpResponse(status=429)
    assert lockout_response(RequestFactory().post(LOGIN), own, {}) is own


def test_other_addresses_still_get_plain_text(db):
    response = lockout_response(RequestFactory().post("/django-admin/login/"), None, {})
    assert response.status_code == 429 and response["Content-Type"].startswith("text/plain")

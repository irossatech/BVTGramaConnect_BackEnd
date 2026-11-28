"""An app login lasts at most JWT_ABSOLUTE_DAYS (90), however often it is renewed (BVTGC-73).

The refresh token carries the login time ("auth_time"); every renewal keeps it. After the limit,
renewal answers 401 session_revoked and the app shows the login screen. Tokens issued before this
change carry no login time: their count starts at their first renewal, so nobody is logged out on
release day.
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.tokens import LOGIN_TIME_CLAIM, TOKEN_VERSION_CLAIM, issue_tokens

pytestmark = pytest.mark.django_db
REFRESH = "/api/v1/auth/refresh/"
DAY = 86400


def renew(raw):
    return APIClient().post(REFRESH, {"refresh": raw}, format="json")


def aged(raw, days):
    """The same token (same jti, still outstanding) with its login time `days` in the past."""
    token = RefreshToken(raw)
    token[LOGIN_TIME_CLAIM] = int(timezone.now().timestamp()) - days * DAY
    return str(token)


def login_time(raw):
    return RefreshToken(raw)[LOGIN_TIME_CLAIM]


def test_a_login_records_its_time_and_renewals_keep_it(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    started = login_time(tokens["refresh"])
    assert abs(started - timezone.now().timestamp()) < 60
    renewed = renew(tokens["refresh"])
    assert renewed.status_code == 200
    assert login_time(renewed.json()["refresh"]) == started  # a renewal is not a new login


def test_day_89_renews_and_keeps_counting(world, settings):
    assert settings.JWT_ABSOLUTE_DAYS == 90
    raw = aged(issue_tokens(world.users["va_kolakaluru"])["refresh"], 89)
    started = login_time(raw)  # read before renewing: a renewed token is blacklisted
    renewed = renew(raw)
    assert renewed.status_code == 200
    assert login_time(renewed.json()["refresh"]) == started  # still 89 days old


def test_day_91_is_refused_and_the_app_must_log_in_again(world):
    raw = aged(issue_tokens(world.users["va_kolakaluru"])["refresh"], 91)
    response = renew(raw)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_revoked"
    assert renew(raw).status_code == 401  # and it stays refused


def test_a_new_login_starts_a_new_count(api, world):
    old = aged(issue_tokens(world.users["va_kolakaluru"])["refresh"], 91)
    assert renew(old).status_code == 401
    fresh = issue_tokens(world.users["va_kolakaluru"])["refresh"]  # what POST /auth/login/ returns
    assert renew(fresh).status_code == 200


def test_a_token_from_before_the_change_starts_counting_at_its_first_renewal(world):
    user = world.users["va_kolakaluru"]
    token = RefreshToken.for_user(user)  # as issue_tokens made them before BVTGC-73: no login time
    token[TOKEN_VERSION_CLAIM] = user.token_version
    renewed = renew(str(token))
    assert renewed.status_code == 200  # not logged out on release day
    started = login_time(renewed.json()["refresh"])
    assert abs(started - timezone.now().timestamp()) < 60


def test_zero_means_no_limit(world, settings):
    settings.JWT_ABSOLUTE_DAYS = 0
    raw = aged(issue_tokens(world.users["va_kolakaluru"])["refresh"], 400)
    assert renew(raw).status_code == 200


def test_the_limit_is_the_setting(world, settings):
    settings.JWT_ABSOLUTE_DAYS = 1  # what a qa server may use to test it in a day
    assert renew(aged(issue_tokens(world.users["va_kolakaluru"])["refresh"], 2)).status_code == 401
    assert renew(issue_tokens(world.users["va_kolakaluru"])["refresh"]).status_code == 200

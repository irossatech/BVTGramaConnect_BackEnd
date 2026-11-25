"""A refresh whose answer was lost does not log the official out (BVTGC-93).

On 2G/3G the server's answer to POST /auth/refresh/ can be lost after the old refresh token was used. The
app then sends the old token again. Within JWT_REFRESH_GRACE_SECONDS of its first use, and only while its
replacement is unused, the server ends that lost replacement and gives a new pair: one usable chain.
Everything that ends a login still ends it: a used replacement, logout, block, reset, the login age.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RefreshRotation
from apps.accounts.tokens import issue_tokens

pytestmark = pytest.mark.django_db
REFRESH = "/api/v1/auth/refresh/"
ME = "/api/v1/me/"


def refresh(token):
    return APIClient().post(REFRESH, {"refresh": token}, format="json")


def works(token) -> bool:
    return refresh(token).status_code == 200


def me(access):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client.get(ME)


@pytest.fixture
def lost_answer(world):
    """The official's refresh went through on the server, but the app never got the answer."""
    first = issue_tokens(world.users["va_kolakaluru"])
    lost = refresh(first["refresh"])
    assert lost.status_code == 200
    return first["refresh"], lost.json()["refresh"]


def test_sending_the_old_token_again_keeps_the_official_logged_in(lost_answer):
    old, lost = lost_answer
    again = refresh(old)
    assert again.status_code == 200, again.json()
    assert set(again.json()) == {"access", "refresh"}
    assert again.json()["refresh"] not in (old, lost)
    assert me(again.json()["access"]).status_code == 200
    # One usable chain: the lost replacement has ended, the new one works.
    assert refresh(lost).status_code == 401
    assert works(again.json()["refresh"])


def test_retries_inside_the_window_keep_working(lost_answer):
    old, _ = lost_answer
    second = refresh(old).json()["refresh"]  # this answer was lost too
    third = refresh(old)
    assert third.status_code == 200
    assert refresh(second).status_code == 401
    assert works(third.json()["refresh"])


def test_after_the_window_the_old_token_is_refused(lost_answer, settings):
    old, lost = lost_answer
    RefreshRotation.objects.update(at=timezone.now() - timedelta(seconds=settings.JWT_REFRESH_GRACE_SECONDS + 1))
    response = refresh(old)
    assert response.status_code == 401 and response.json()["error"]["code"] == "session_revoked"
    assert works(lost)  # the replacement is untouched


def test_the_window_counts_from_the_first_use_and_never_grows(lost_answer, settings):
    old, _ = lost_answer
    started = RefreshRotation.objects.get().at
    refresh(old)
    assert RefreshRotation.objects.get().at == started


def test_no_grace_once_the_replacement_was_used(lost_answer):
    old, lost = lost_answer
    newer = refresh(lost)
    assert newer.status_code == 200
    assert refresh(old).status_code == 401
    assert works(newer.json()["refresh"])  # the refusal changes nothing


def test_no_grace_after_logging_out_the_replacement(lost_answer, world):
    old, lost = lost_answer
    access = issue_tokens(world.users["va_kolakaluru"])["access"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert client.post("/api/v1/auth/logout/", {"refresh": lost}, format="json").status_code == 204
    assert refresh(old).status_code == 401


def test_no_grace_after_logging_out_the_old_token_itself(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert client.post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, format="json").status_code == 204
    assert refresh(tokens["refresh"]).status_code == 401


@pytest.mark.parametrize("action", ["block", "reset-password"])
def test_no_grace_after_a_block_or_reset(lost_answer, api, world, action):
    old, _ = lost_answer
    target = world.users["va_kolakaluru"]
    done = api(world.users["ma_tenali"]).post(f"/api/v1/users/{target.id}/{action}/", {}, format="json")
    assert done.status_code == 200, done.json()
    assert refresh(old).status_code == 401


def test_no_grace_after_log_out_everywhere(lost_answer, world):
    old, _ = lost_answer
    access = issue_tokens(world.users["va_kolakaluru"])["access"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert client.post("/api/v1/auth/logout/", {"all_devices": True}, format="json").status_code == 204
    assert refresh(old).status_code == 401


def test_no_grace_for_a_login_past_its_age(world, settings):
    from rest_framework_simplejwt.tokens import RefreshToken

    user = world.users["va_kolakaluru"]
    token = RefreshToken.for_user(user)
    token["tv"] = user.token_version
    token["auth_time"] = int((timezone.now() - timedelta(days=2)).timestamp())
    assert works(str(token))  # the answer is lost; meanwhile the login passes its age
    settings.JWT_ABSOLUTE_DAYS = 1
    assert refresh(str(token)).status_code == 401


def test_the_grace_can_be_switched_off(lost_answer, settings):
    settings.JWT_REFRESH_GRACE_SECONDS = 0
    old, lost = lost_answer
    assert refresh(old).status_code == 401
    assert works(lost)


def test_only_token_ids_are_kept(lost_answer):
    old, lost = lost_answer
    row = RefreshRotation.objects.get()
    assert old not in row.old_jti and lost not in row.new_jti
    assert len(row.old_jti) <= 64 and len(row.new_jti) <= 64


def test_housekeeping_removes_old_rotations(lost_answer):
    from grama.housekeeping import run_housekeeping

    RefreshRotation.objects.update(at=timezone.now() - timedelta(days=2))
    run_housekeeping()
    assert not RefreshRotation.objects.exists()

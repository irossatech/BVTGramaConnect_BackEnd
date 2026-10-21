"""JWTs for the mobile app: token_version claim, immediate revocation on block, refresh, logout."""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import RoleAssignment
from apps.accounts.tokens import issue_tokens

pytestmark = pytest.mark.django_db

ME = "/api/v1/me/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"


def bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def error_code(response):
    return response.json()["error"]["code"]


def test_tokens_carry_the_token_version(world):
    user = world.users["va_kolakaluru"]
    tokens = issue_tokens(user)
    assert AccessToken(tokens["access"])["tv"] == user.token_version == 0


def test_me_needs_a_token(api, world):
    response = api().get(ME)
    assert response.status_code == 401
    assert error_code(response) == "not_authenticated"


def test_blocking_ends_existing_sessions_at_once(api, world):
    target = world.users["pm_kolakaluru"]
    tokens = issue_tokens(target)
    assert bearer(tokens["access"]).get(ME).status_code == 200

    blocked = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/block/", {}, format="json")
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "BLOCKED"

    after = bearer(tokens["access"]).get(ME)
    assert after.status_code == 401
    assert error_code(after) == "session_revoked"
    assert "WWW-Authenticate" in after
    refresh = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert refresh.status_code == 401
    assert error_code(refresh) == "session_revoked"


def test_unblocking_does_not_revive_old_tokens(api, world):
    target = world.users["pm_kolakaluru"]
    old = issue_tokens(target)
    admin = api(world.users["va_kolakaluru"])
    admin.post(f"/api/v1/users/{target.id}/block/", {}, format="json")
    admin.post(f"/api/v1/users/{target.id}/unblock/", {}, format="json")
    assert bearer(old["access"]).get(ME).status_code == 401
    target.refresh_from_db()
    assert bearer(issue_tokens(target)["access"]).get(ME).status_code == 200


def test_refresh_rotates_and_blacklists_the_old_token(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    first = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200
    assert set(first.json()) == {"access", "refresh"}
    assert bearer(first.json()["access"]).get(ME).status_code == 200
    replay = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401
    assert error_code(replay) == "session_revoked"
    assert APIClient().post(REFRESH, {"refresh": first.json()["refresh"]}, format="json").status_code == 200


def test_logout_this_device(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    response = bearer(tokens["access"]).post(LOGOUT, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 204
    assert APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_logout_everywhere_ends_every_session(world):
    user = world.users["va_kolakaluru"]
    phone, tablet = issue_tokens(user), issue_tokens(user)
    response = bearer(phone["access"]).post(LOGOUT, {"all_devices": True}, format="json")
    assert response.status_code == 204
    assert error_code(bearer(tablet["access"]).get(ME)) == "session_revoked"
    assert APIClient().post(REFRESH, {"refresh": tablet["refresh"]}, format="json").status_code == 401


def test_expired_access_token_says_refresh(world):
    token = AccessToken.for_user(world.users["va_kolakaluru"])
    token["tv"] = 0
    token.set_exp(lifetime=-timedelta(seconds=1))
    response = bearer(str(token)).get(ME)
    assert response.status_code == 401
    assert error_code(response) == "token_expired"


def test_garbage_token_is_invalid(world):
    response = bearer("not.a.token").get(ME)
    assert response.status_code == 401
    assert error_code(response) == "token_invalid"


def test_token_without_version_claim_is_refused(world):
    token = AccessToken.for_user(world.users["va_kolakaluru"])
    assert error_code(bearer(str(token)).get(ME)) == "session_revoked"


def test_refresh_without_a_token(db):
    response = APIClient().post(REFRESH, {}, format="json")
    assert response.status_code == 401
    assert error_code(response) == "session_revoked"


def test_removed_role_stops_counting_on_the_next_request(api, world):
    user = world.users["va_kolakaluru"]
    client = api(user)
    assert client.get("/api/v1/users/").status_code == 200
    RoleAssignment.objects.filter(user=user).delete()
    response = client.get("/api/v1/users/")
    assert response.status_code == 403
    assert client.get(ME).json()["role_assignments"] == []

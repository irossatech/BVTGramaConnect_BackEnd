"""Broken, stolen or stale credentials never get in (audit stage S6, BVTGC-17).

The app's JWT: no token, a malformed one, a changed signature, an expired access token, a refresh
token sent as an access token, a refresh token after logout (blacklisted), and the token of a blocked
official. The console's session: none, a write without the CSRF token, and a session of a role
without the console permission. Every refusal comes in the error envelope with a contract code
(docs/api-contract.md, section 1 and section 2).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.accounts.tokens import issue_tokens
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

ME = "/api/v1/me/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
DASHBOARD = "/api/v1/admin/dashboard/"
SETTINGS = "/api/v1/admin/settings/"


def bearer(token: str) -> APIClient:
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")


def refused(response, status: int, code: str) -> None:
    assert response.status_code == status, response.content
    error = response.json()["error"]
    assert error["code"] == code and error["message"]


# --- The app's JWT -----------------------------------------------------------------------------------


def test_no_token(world):
    refused(APIClient().get(ME), 401, "not_authenticated")


@pytest.mark.parametrize("header", ["Bearer", "Bearer not-a-token", "Bearer a.b.c", "Token abc", "Bearer " + "x" * 5000])
def test_a_malformed_token(world, header):
    response = APIClient(HTTP_AUTHORIZATION=header).get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] in ("token_invalid", "not_authenticated")


def test_a_token_with_a_changed_signature(world):
    access = issue_tokens(world.users["va_kolakaluru"])["access"]
    head, body, signature = access.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    refused(bearer(f"{head}.{body}.{flipped}").get(ME), 401, "token_invalid")


def test_a_token_whose_body_was_changed_to_another_user(world):
    """Swapping the user id inside a signed token breaks the signature."""
    import base64
    import json

    access = issue_tokens(world.users["pm_kolakaluru"])["access"]
    head, body, signature = access.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["user_id"] = str(world.users["super"].pk)
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    refused(bearer(f"{head}.{forged}.{signature}").get(ME), 401, "token_invalid")


def test_an_expired_access_token(world):
    token = AccessToken.for_user(world.users["va_kolakaluru"])
    token["tv"] = world.users["va_kolakaluru"].token_version
    token.set_exp(lifetime=-timedelta(seconds=1))
    refused(bearer(str(token)).get(ME), 401, "token_expired")


def test_a_refresh_token_is_not_an_access_token(world):
    refresh = issue_tokens(world.users["va_kolakaluru"])["refresh"]
    refused(bearer(refresh).get(ME), 401, "token_invalid")


def test_an_access_token_is_not_a_refresh_token(world):
    access = issue_tokens(world.users["va_kolakaluru"])["access"]
    response = APIClient().post(REFRESH, {"refresh": access}, format="json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] in ("token_invalid", "session_revoked")


def test_a_refresh_token_after_logout_is_refused(world):
    tokens = issue_tokens(world.users["va_kolakaluru"])
    assert bearer(tokens["access"]).post(LOGOUT, {"refresh": tokens["refresh"]}, format="json").status_code == 204
    response = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] in ("token_invalid", "session_revoked")


def test_a_blocked_officials_tokens_stop_at_once(world, api):
    official = world.users["va_kolakaluru"]
    tokens = issue_tokens(official)
    assert bearer(tokens["access"]).get(ME).status_code == 200
    blocked = api(world.users["super"]).post(f"/api/v1/users/{official.pk}/block/", {"comment": "test"}, format="json")
    assert blocked.status_code == 200
    refused(bearer(tokens["access"]).get(ME), 401, "session_revoked")
    response = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 401


def test_a_deactivated_user_without_the_block_action_is_refused(world):
    """Even if is_active were switched off some other way (a shell), the old token gets no answer."""
    official = world.users["va_kolakaluru"]
    access = issue_tokens(official)["access"]
    User.objects.filter(pk=official.pk).update(is_active=False)
    response = bearer(access).get(ME)
    assert response.status_code == 401


# --- The console's session ---------------------------------------------------------------------------


def session_for(username: str) -> APIClient:
    """A real console login, CSRF checks on, as the browser would have it."""
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/v1/auth/csrf/")
    response = client.post("/api/v1/auth/session/login/", {"username": username, "password": TEST_PASSWORD},
                           format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    assert response.status_code == 200, response.content
    return client


def test_no_session(world):
    refused(APIClient(enforce_csrf_checks=True).get(DASHBOARD), 401, "not_authenticated")


def test_a_session_reads_without_the_csrf_token(world):
    assert session_for("super").get(DASHBOARD).status_code == 200


def test_a_session_write_without_the_csrf_token_is_refused(world):
    client = session_for("super")
    response = client.patch(SETTINGS, {"review_escalation_hours": 30}, format="json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] in ("csrf_failed", "permission_denied")


def test_a_session_write_with_the_csrf_token_works(world):
    client = session_for("super")
    response = client.patch(SETTINGS, {"review_escalation_hours": 30}, format="json",
                            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    assert response.status_code == 200, response.content


@pytest.mark.parametrize("username", ["pm_kolakaluru", "va_kolakaluru", "da_guntur"])
def test_a_role_without_the_console_permission_gets_no_session(world, username):
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/v1/auth/csrf/")
    response = client.post("/api/v1/auth/session/login/", {"username": username, "password": TEST_PASSWORD},
                           format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    assert response.status_code == 403
    assert client.get(DASHBOARD).status_code == 401


def test_a_session_made_some_other_way_for_a_role_without_the_console_permission_is_refused(world):
    """A session cookie for a Village Admin (made outside the login) still does not open the console."""
    client = APIClient()
    client.force_login(world.users["va_kolakaluru"])
    response = client.get(DASHBOARD)
    assert response.status_code in (401, 403)
    assert "error" in response.json()

"""Username + password login for the app (JWT) and the console (session), lockout, password changes,
admin resets, blocking. docs/authorization.md, sections 1 to 4."""

from datetime import timedelta

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth.hashers import identify_hasher
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
CHANGE = "/api/v1/auth/password/change/"
CSRF = "/api/v1/auth/csrf/"
SESSION_LOGIN = "/api/v1/auth/session/login/"
SESSION_LOGOUT = "/api/v1/auth/session/logout/"
ME = "/api/v1/me/"
NEW_PASSWORD = "Kolakaluru-Panchayat-77"


def login(username, password=TEST_PASSWORD, http=None):
    http = http or APIClient()
    return http.post(LOGIN, {"username": username, "password": password}, format="json")


def code(response):
    return response.json()["error"]["code"]


def bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def console_session(username, password=TEST_PASSWORD, ip="127.0.0.1"):
    """Log in the way the console does: CSRF cookie first, then the session login with X-CSRFToken."""
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=ip)
    client.get(CSRF)
    token = client.cookies["csrftoken"].value
    response = client.post(
        SESSION_LOGIN, {"username": username, "password": password}, format="json", HTTP_X_CSRFTOKEN=token
    )
    # Login rotates the CSRF token (Django does this on every login): read the new cookie.
    client.csrf = client.cookies["csrftoken"].value
    return client, response


def post_csrf(client, url, data=None):
    return client.post(url, data or {}, format="json", HTTP_X_CSRFTOKEN=client.csrf)


# --- App login (JWT) -----------------------------------------------------------------------------


@pytest.mark.parametrize("username", ["va_kolakaluru", "VA_Kolakaluru", "  VA_KOLAKALURU "])
def test_app_login_ignores_case(world, username):
    response = login(username)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"access", "refresh", "user"}
    assert data["user"]["username"] == "va_kolakaluru"
    assert data["user"]["must_change_password"] is False
    assert bearer(data["access"]).get(ME).status_code == 200


def test_email_is_not_a_login_name(world):
    assert code(login("va_kolakaluru@example.org")) == "invalid_credentials"


def test_wrong_password_and_unknown_user_answer_the_same(world):
    wrong = login("va_kolakaluru", "not-the-password")
    unknown = login("nobody", "not-the-password")
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json() == unknown.json()
    assert code(wrong) == "invalid_credentials"


def test_login_and_failed_login_are_audited(world):
    login("va_kolakaluru")
    login("va_kolakaluru", "wrong-password")
    user = world.users["va_kolakaluru"]
    assert AuditLog.objects.filter(action="auth.login", actor=user, via="app").exists()
    failed = AuditLog.objects.get(action="auth.login_failed")
    assert failed.target_id == str(user.id) and failed.data == {"username": "va_kolakaluru", "locked": False}
    assert "wrong-password" not in str(failed.data)
    user.refresh_from_db()
    assert user.last_login is not None


def test_blocked_user_cannot_log_in(world):
    User.objects.filter(pk=world.users["pm_kaza"].pk).update(is_active=False)
    response = login("pm_kaza")
    assert response.status_code == 403
    assert code(response) == "account_blocked"
    assert AuditLog.objects.filter(action="auth.login_refused", data__reason="account_blocked").exists()


def test_blocked_user_with_a_wrong_password_learns_nothing(world):
    User.objects.filter(pk=world.users["pm_kaza"].pk).update(is_active=False)
    assert code(login("pm_kaza", "wrong")) == "invalid_credentials"


def test_user_without_an_active_role_cannot_log_in(world):
    RoleAssignment.objects.filter(user=world.users["pm_kaza"]).update(is_active=False)
    response = login("pm_kaza")
    assert response.status_code == 403
    assert code(response) == "permission_denied"


def test_old_otp_endpoints_are_gone(world):
    assert APIClient().post("/api/v1/auth/otp/request/", {"mobile": "9000000006"}, format="json").status_code == 404


def test_role_default_passwords_are_gone(api, world):
    assert api(world.users["super"]).get("/api/v1/admin/role-passwords/").status_code == 404


# --- Passwords: hashing and rules --------------------------------------------------------------


def test_argon2_is_the_production_hasher():
    from grama.settings import base

    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    import argon2  # noqa: F401  (installed from requirements.txt)


def test_new_hashes_use_the_first_hasher(world):
    user = world.users["va_kolakaluru"]
    assert identify_hasher(user.password).algorithm == "md5"  # test settings keep it fast


def test_validators_are_on():
    names = [entry["NAME"].rsplit(".", 1)[-1] for entry in settings.AUTH_PASSWORD_VALIDATORS]
    assert names == [
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
        "PasswordCompositionValidator",
    ]
    assert settings.AUTH_PASSWORD_VALIDATORS[1]["OPTIONS"] == {"min_length": 8}


# --- Lockout (django-axes) ---------------------------------------------------------------------


def test_five_failures_lock_the_username_and_ip_for_fifteen_minutes(world):
    for _ in range(4):
        assert code(login("va_kolakaluru", "wrong")) == "invalid_credentials"
    fifth = login("va_kolakaluru", "wrong")
    assert fifth.status_code == 429 and code(fifth) == "locked_out"
    locked = login("va_kolakaluru")  # even the right password waits
    assert locked.status_code == 429
    error = locked.json()["error"]
    assert error["code"] == "locked_out" and 800 < error["retry_after_s"] <= 901
    # Another address is not locked.
    assert login("va_kolakaluru", http=APIClient(REMOTE_ADDR="10.9.9.8")).status_code == 200
    # Fifteen minutes later the pair may try again.
    AccessAttempt.objects.update(attempt_time=timezone.now() - timedelta(minutes=16))
    assert login("va_kolakaluru").status_code == 200


def test_tries_during_a_lockout_do_not_extend_it(world):
    for _ in range(5):
        login("va_kolakaluru", "wrong")
    first = AccessAttempt.objects.get().attempt_time
    login("va_kolakaluru", "wrong")
    login("va_kolakaluru")
    assert AccessAttempt.objects.get().attempt_time == first


def test_lockout_is_per_username(world):
    for _ in range(5):
        login("va_kolakaluru", "wrong")
    assert login("ma_tenali").status_code == 200


def test_a_success_resets_the_count(world):
    for _ in range(4):
        login("va_kolakaluru", "wrong")
    assert login("va_kolakaluru").status_code == 200
    for _ in range(4):
        login("va_kolakaluru", "wrong")
    assert login("va_kolakaluru").status_code == 200


def test_lockout_is_audited_once(world):
    for _ in range(7):
        login("va_kolakaluru", "wrong")
    entry = AuditLog.objects.get(action="auth.locked_out")
    assert entry.target_id == str(world.users["va_kolakaluru"].id)
    assert entry.data == {"username": "va_kolakaluru", "minutes": 15}
    assert AuditLog.objects.filter(action="auth.login_failed").count() == 7


def lock(username):
    for _ in range(5):
        login(username, "wrong")


def test_super_admin_unlocks(api, world):
    lock("va_kolakaluru")
    target = world.users["va_kolakaluru"]
    client = api(world.users["super"])
    row = client.get(f"/api/v1/users/{target.id}/").json()
    assert row["locked_out"] is True and "unlock" in row["allowed_actions"]
    response = client.post(f"/api/v1/users/{target.id}/unlock/", {}, format="json")
    assert response.status_code == 200
    assert response.json()["locked_out"] is False and "unlock" not in response.json()["allowed_actions"]
    assert login("va_kolakaluru").status_code == 200
    entry = AuditLog.objects.get(action="user.unlocked")
    assert entry.actor == world.users["super"] and entry.data == {"attempts_cleared": 1}


@pytest.mark.parametrize(
    "actor, status",
    [("ma_tenali", 200), ("da_guntur", 200), ("va_kolakaluru", 404), ("ma_mangalagiri", 404), ("pm_kolakaluru", 403)],
)
def test_unlock_follows_the_management_rules(api, world, actor, status):
    lock("va_kolakaluru")
    target = world.users["va_kolakaluru"]
    response = api(world.users[actor]).post(f"/api/v1/users/{target.id}/unlock/", {}, format="json")
    assert response.status_code == status
    assert AccessAttempt.objects.exists() == (status != 200)


# --- Forced change at first login --------------------------------------------------------------


@pytest.fixture
def must_change(world):
    user = world.users["va_kolakaluru"]
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    user.refresh_from_db()
    return user


def test_forced_change_blocks_every_other_endpoint(must_change):
    client = bearer(login("va_kolakaluru").json()["access"])
    me = client.get(ME)
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    for method, url in [
        ("get", "/api/v1/users/"),
        ("get", "/api/v1/review/queue/"),
        ("patch", ME),
        ("get", "/api/v1/posts/"),
        ("get", "/api/v1/stats/"),
    ]:
        response = getattr(client, method)(url, {}, format="json")
        assert response.status_code == 403, url
        assert code(response) == "password_change_required"


def test_password_change_unlocks_and_ends_other_sessions(must_change):
    phone = login("va_kolakaluru").json()
    tablet = login("va_kolakaluru").json()
    response = bearer(phone["access"]).post(
        CHANGE, {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}, format="json"
    )
    assert response.status_code == 200
    assert set(response.json()) == {"access", "refresh"}
    fresh = bearer(response.json()["access"])
    assert fresh.get("/api/v1/users/").status_code == 200
    assert fresh.get(ME).json()["must_change_password"] is False
    assert code(bearer(tablet["access"]).get(ME)) == "session_revoked"
    assert code(bearer(phone["access"]).get(ME)) == "session_revoked"
    assert APIClient().post(REFRESH, {"refresh": tablet["refresh"]}, format="json").status_code == 401
    assert login("va_kolakaluru").status_code == 400
    assert login("va_kolakaluru", NEW_PASSWORD).status_code == 200
    must_change.refresh_from_db()
    assert must_change.password_changed_at is not None
    entry = AuditLog.objects.get(action="user.password_changed")
    assert NEW_PASSWORD not in str(entry.data)


@pytest.mark.parametrize(
    "current, new, error_code",
    [
        ("wrong", NEW_PASSWORD, "invalid_credentials"),
        (TEST_PASSWORD, "12345678", "validation_error"),
        (TEST_PASSWORD, "Ab1-xyz", "validation_error"),  # 7 characters
        (TEST_PASSWORD, "password", "validation_error"),
        (TEST_PASSWORD, "va_kolakaluru1", "validation_error"),  # too close to the username
        (TEST_PASSWORD, TEST_PASSWORD, "validation_error"),
    ],
)
def test_password_change_validation(world, current, new, error_code):
    client = bearer(login("va_kolakaluru").json()["access"])
    response = client.post(CHANGE, {"current_password": current, "new_password": new}, format="json")
    assert response.status_code == 400
    assert code(response) == error_code
    if error_code == "validation_error":
        assert response.json()["error"]["fields"]["new_password"]


def test_eight_characters_are_enough(world):
    client = bearer(login("va_kolakaluru").json()["access"])
    response = client.post(CHANGE, {"current_password": TEST_PASSWORD, "new_password": "Pq7-wxyz"}, format="json")
    assert response.status_code == 200


# --- Password composition: a capital, a small letter, a number, a special character ----------------


@pytest.mark.parametrize(
    "new, missing",
    [
        ("pq7-wxyzab", ["capital letter"]),
        ("PQ7-WXYZAB", ["small letter"]),
        ("Pqr-wxyzab", ["number"]),
        ("Pq7wxyzab9", ["special character"]),
        ("pqrwxyzabc", ["capital letter", "number", "special character"]),  # all named in one message
    ],
)
def test_a_new_password_needs_every_kind_of_character(world, new, missing):
    client = bearer(login("va_kolakaluru").json()["access"])
    response = client.post(CHANGE, {"current_password": TEST_PASSWORD, "new_password": new}, format="json")
    assert response.status_code == 400 and code(response) == "validation_error"
    message = " ".join(response.json()["error"]["fields"]["new_password"])
    assert all(part in message for part in missing), message


@pytest.mark.parametrize("new", ["Pq7@wxyz", "Gram#2026x", "Abc 12345!", "తెలుగుAb1$"])
def test_any_special_character_counts(world, new):
    client = bearer(login("va_kolakaluru").json()["access"])
    response = client.post(CHANGE, {"current_password": TEST_PASSWORD, "new_password": new}, format="json")
    assert response.status_code == 200, response.json()


def test_admins_typed_temporary_passwords_follow_the_rule_too(api, world):
    refused = reset(api, world, "va_kolakaluru", password="GramReset2026")  # no special character
    assert refused.status_code == 400 and "special character" in " ".join(refused.json()["error"]["fields"]["password"])
    body = {"name": "New Member", "username": "new.member", "role": "PANCHAYAT_MEMBER",
            "area": world.area_json("kolakaluru"), "password": "gram-reset-2026"}  # no capital letter
    created = api(world.users["va_kolakaluru"]).post("/api/v1/users/", body, format="json")
    assert created.status_code == 400 and "capital letter" in " ".join(created.json()["error"]["fields"]["password"])


def test_generated_temporary_passwords_always_pass_every_rule(world):
    from django.contrib.auth.password_validation import validate_password

    from apps.accounts.credentials import PASSWORD_SPECIALS, generate_password

    user = world.users["pm_kolakaluru"]
    for _ in range(300):
        password = generate_password()
        assert len(password) == 12 and sum(ch in PASSWORD_SPECIALS for ch in password) == 1
        validate_password(password, user)  # raises if any rule fails


def test_existing_passwords_keep_working(world):
    """The rule applies when a password is next set, not to passwords people already have."""
    user = world.users["pm_kolakaluru"]
    user.set_password("oldstyle2024")  # set before the rule existed
    user.save(update_fields=["password"])
    assert login("pm_kolakaluru", "oldstyle2024").status_code == 200


# --- Refresh and logout (app) ------------------------------------------------------------------


def test_logout_blacklists_the_refresh_token(world):
    tokens = login("va_kolakaluru").json()
    assert bearer(tokens["access"]).post(LOGOUT, {"refresh": tokens["refresh"]}, format="json").status_code == 204
    replay = APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401 and code(replay) == "session_revoked"


# --- Admin reset (forgotten password) ------------------------------------------------------------


def reset(api, world, actor, target="pm_kolakaluru", **body):
    return api(world.users[actor]).post(f"/api/v1/users/{world.users[target].id}/reset-password/", body, format="json")


def test_reset_generates_a_temporary_password_shown_once(api, world):
    target = world.users["pm_kolakaluru"]
    old = issue_tokens(target)
    response = reset(api, world, "va_kolakaluru")
    assert response.status_code == 200
    body = response.json()
    temporary = body["temporary_password"]
    assert len(temporary) == 12 and body["user"]["must_change_password"] is True
    assert code(bearer(old["access"]).get(ME)) == "session_revoked"
    assert login("pm_kolakaluru").status_code == 400
    first = login("pm_kolakaluru", temporary)
    assert first.status_code == 200 and first.json()["user"]["must_change_password"] is True
    # Shown once: reading the user again never shows it.
    again = api(world.users["va_kolakaluru"]).get(f"/api/v1/users/{target.id}/").json()
    assert "temporary_password" not in again
    entry = AuditLog.objects.get(action="user.password_reset")
    assert entry.data == {"password": "generated"} and temporary not in str(entry.data)


def test_reset_with_a_chosen_password(api, world):
    response = reset(api, world, "va_kolakaluru", password="Gram-Reset-2026")
    assert response.status_code == 200
    assert "temporary_password" not in response.json()
    assert login("pm_kolakaluru", "Gram-Reset-2026").json()["user"]["must_change_password"] is True


@pytest.mark.parametrize("password", ["short", "12345678", "password1"])
def test_reset_refuses_weak_passwords(api, world, password):
    response = reset(api, world, "va_kolakaluru", password=password)
    assert response.status_code == 400 and response.json()["error"]["fields"]["password"]
    assert login("pm_kolakaluru").status_code == 200


def test_reset_clears_a_lockout(api, world):
    lock("pm_kolakaluru")
    temporary = reset(api, world, "va_kolakaluru").json()["temporary_password"]
    assert login("pm_kolakaluru", temporary).status_code == 200


@pytest.mark.parametrize(
    "actor, target, status",
    [
        ("va_kolakaluru", "pm_kolakaluru", 200),
        ("ma_tenali", "pm_kolakaluru", 200),
        ("va_nandivelugu", "pm_kolakaluru", 404),
        ("pm_nandivelugu", "pm_kolakaluru", 403),
        ("ma_tenali", "va_kolakaluru", 200),
        ("va_kolakaluru", "va_nandivelugu", 404),  # same rank
        ("ma_tenali", "ma_mangalagiri", 404),  # same rank
        ("ma_tenali", "da_guntur", 404),  # higher rank
        ("da_guntur", "super", 404),
        ("super", "da_krishna", 200),
    ],
)
def test_reset_follows_the_management_rules(api, world, actor, target, status):
    response = reset(api, world, actor, target)
    assert response.status_code == status
    world.users[target].refresh_from_db()
    assert world.users[target].check_password(TEST_PASSWORD) == (status != 200)


def test_nobody_resets_their_own_password_this_way(api, world):
    assert reset(api, world, "super", "super").status_code == 404


# --- Blocking ---------------------------------------------------------------------------------


def test_blocked_user_is_rejected_at_once_everywhere(api, world):
    target = world.users["pm_kolakaluru"]
    tokens = login("pm_kolakaluru").json()
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/block/", {"reason": "x"}, format="json")
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert response.json()["status"] == "BLOCKED"
    assert code(bearer(tokens["access"]).get(ME)) == "session_revoked"
    assert code(APIClient().post(REFRESH, {"refresh": tokens["refresh"]}, format="json")) == "session_revoked"
    assert code(login("pm_kolakaluru")) == "account_blocked"
    assert AuditLog.objects.filter(action="user.blocked", data={"reason": "x"}).exists()


def test_blocking_ends_console_sessions_too(api, world):
    client, response = console_session("super")
    assert response.status_code == 200
    target = world.users["super"]
    from apps.accounts.tokens import block_user

    block_user(target)
    after = client.get(ME)
    assert after.status_code == 401 and code(after) == "session_revoked"
    assert client.get(ME).status_code == 401  # the session is gone, not just refused once


# --- Console session -----------------------------------------------------------------------------


def test_csrf_endpoint_sets_a_readable_cookie(db):
    client = APIClient()
    response = client.get(CSRF)
    assert response.status_code == 200 and response.json() == {"detail": "ok"}
    cookie = client.cookies["csrftoken"]
    assert cookie.value and not cookie["httponly"]


def test_console_login_creates_a_session(world):
    client, response = console_session("SUPER")
    assert response.status_code == 200
    assert set(response.json()) == {"user"}
    assert "web_admin.access_web_console" in response.json()["user"]["permissions"]
    cookie = client.cookies["sessionid"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert client.get(ME).json()["username"] == "super"
    assert client.get("/api/v1/admin/dashboard/").status_code == 200
    assert AuditLog.objects.filter(action="auth.login", via="console").exists()


def test_console_login_needs_the_csrf_token(world):
    client = APIClient(enforce_csrf_checks=True)
    client.get(CSRF)
    response = client.post(SESSION_LOGIN, {"username": "super", "password": TEST_PASSWORD}, format="json")
    assert response.status_code == 403
    assert "sessionid" not in client.cookies


@pytest.mark.parametrize("name", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_console_is_for_super_admins_only(world, name):
    client, response = console_session(name)
    assert response.status_code == 403 and code(response) == "not_console_user"
    assert not client.cookies.get("sessionid") or not client.cookies["sessionid"].value
    assert client.get(ME).status_code == 401
    assert AuditLog.objects.filter(action="auth.login_refused", data__reason="not_console_user").exists()


def test_console_page_and_api_refuse_non_super_admins_even_with_a_session(console, world):
    """A session made some other way (Django admin) still cannot use the console API."""
    client = console(world.users["da_guntur"])
    response = client.get("/api/v1/admin/dashboard/")
    assert response.status_code == 403 and code(response) == "not_console_user"


def test_console_login_lockout_and_wrong_password(world):
    for _ in range(4):
        _client, response = console_session("super", "wrong")
        assert code(response) == "invalid_credentials"
    _client, response = console_session("super", "wrong")
    assert response.status_code == 429 and code(response) == "locked_out"
    assert response.json()["error"]["retry_after_s"] > 0


def test_console_session_needs_csrf_on_writes(world):
    client, _ = console_session("super")
    response = client.patch(ME, {"name_te": "కొత్త"}, format="json")
    assert response.status_code == 403
    assert client.patch(ME, {"name_te": "కొత్త"}, format="json", HTTP_X_CSRFTOKEN=client.csrf).status_code == 200


def test_console_logout_ends_the_session(world):
    client, _ = console_session("super")
    assert post_csrf(client, SESSION_LOGOUT).status_code == 204
    assert client.get(ME).status_code == 401


def test_console_session_expires_after_idle_time(world, settings):
    assert settings.SESSION_COOKIE_AGE == settings.CONSOLE_SESSION_IDLE_MINUTES * 60 == 30 * 60
    assert settings.SESSION_SAVE_EVERY_REQUEST is True
    client, _ = console_session("super")
    from django.contrib.sessions.models import Session

    Session.objects.update(expire_date=timezone.now() - timedelta(seconds=1))
    response = client.get(ME)
    assert response.status_code == 401 and code(response) == "not_authenticated"


def test_new_super_admin_logs_in_to_the_console_and_must_change_first(world):
    User.objects.filter(pk=world.users["super"].pk).update(must_change_password=True)
    client, response = console_session("super")
    assert response.status_code == 200 and response.json()["user"]["must_change_password"] is True
    blocked = client.get("/api/v1/admin/dashboard/")
    assert code(blocked) == "password_change_required"
    changed = post_csrf(client, CHANGE, {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 200 and set(changed.json()) == {"user"}
    assert changed.json()["user"]["must_change_password"] is False
    # The same session carries on; other sessions and tokens ended.
    assert client.get("/api/v1/admin/dashboard/").status_code == 200


def test_console_password_change_ends_other_sessions(world):
    first, _ = console_session("super")
    second, _ = console_session("super")
    app = login("super").json()
    assert post_csrf(first, CHANGE, {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
    assert first.get(ME).status_code == 200
    assert code(second.get(ME)) in ("session_revoked", "not_authenticated")
    assert second.get(ME).status_code == 401
    assert code(bearer(app["access"]).get(ME)) == "session_revoked"


def test_admin_reset_ends_console_sessions(api, world):
    client, _ = console_session("super")
    from apps.accounts.credentials import set_temporary_password
    from apps.accounts.tokens import revoke_all_sessions

    target = world.users["super"]
    set_temporary_password(target, None)
    revoke_all_sessions(target)
    assert client.get(ME).status_code == 401


def test_jwt_still_wins_over_a_session(world):
    """The app sends a Bearer token; a stray console cookie does not change who it is."""
    client, _ = console_session("super")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(world.users['va_kolakaluru'])['access']}")
    assert client.get(ME).json()["username"] == "va_kolakaluru"

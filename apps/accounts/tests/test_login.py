"""Username/password login, throttling, forced first-login change, invites, resets, role defaults."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.credentials import role_default
from apps.accounts.models import LoginAttempt, RoleAssignment, RoleDefaultPassword, User, UserStatus
from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CHANGE = "/api/v1/auth/password/change/"
ME = "/api/v1/me/"
NEW_PASSWORD = "Kolakaluru-Panchayat-77"


def login(username, password=TEST_PASSWORD, http=None, **body):
    http = http or APIClient()
    return http.post(LOGIN, {"username": username, "password": password, **body}, format="json")


def code(response):
    return response.json()["error"]["code"]


def bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


# --- Logging in --------------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", ["va_kolakaluru", "VA_Kolakaluru", "va_kolakaluru@example.org", " VA_KOLAKALURU@EXAMPLE.ORG "])
def test_login_with_username_or_email_in_any_case(world, identifier):
    response = login(identifier)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"access", "refresh", "user"}
    assert data["user"]["username"] == "va_kolakaluru"
    assert data["user"]["email"] == "va_kolakaluru@example.org"
    assert data["user"]["must_change_password"] is False
    assert bearer(data["access"]).get(ME).status_code == 200


def test_wrong_password_and_unknown_user_answer_the_same(world):
    wrong = login("va_kolakaluru", "not-the-password")
    unknown = login("nobody", "not-the-password")
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json() == unknown.json()
    assert code(wrong) == "invalid_credentials"


def test_login_is_audited_and_recorded(world):
    login("va_kolakaluru")
    assert AuditLog.objects.filter(action="auth.login", actor_id=world.users["va_kolakaluru"].id).exists()
    assert LoginAttempt.objects.get().success is True
    world.users["va_kolakaluru"].refresh_from_db()
    assert world.users["va_kolakaluru"].last_login is not None


def test_five_failures_lock_the_username_for_a_while(world):
    for _ in range(5):
        assert code(login("va_kolakaluru", "wrong")) == "invalid_credentials"
    locked = login("va_kolakaluru")  # even the right password waits
    assert locked.status_code == 429
    assert code(locked) == "throttled"
    assert int(locked["Retry-After"]) > 0
    LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=16))
    assert login("va_kolakaluru").status_code == 200


def test_a_success_resets_the_username_count(world):
    for _ in range(4):
        login("va_kolakaluru", "wrong")
    assert login("va_kolakaluru").status_code == 200
    for _ in range(4):
        login("va_kolakaluru", "wrong")
    assert login("va_kolakaluru").status_code == 200


def test_thirty_failures_from_one_ip_block_that_ip(world, settings):
    client = APIClient(REMOTE_ADDR="10.9.9.9")
    for index in range(30):
        login(f"guess{index}", "wrong", http=client)
    assert login("va_kolakaluru", http=client).status_code == 429
    assert login("va_kolakaluru", http=APIClient(REMOTE_ADDR="10.9.9.8")).status_code == 200


def test_blocked_user_cannot_log_in(world):
    User.objects.filter(pk=world.users["pm_kaza"].pk).update(status=UserStatus.BLOCKED)
    response = login("pm_kaza")
    assert response.status_code == 403
    assert code(response) == "account_blocked"


def test_user_without_a_role_cannot_log_in(world):
    RoleAssignment.objects.filter(user=world.users["pm_kaza"]).delete()
    response = login("pm_kaza")
    assert response.status_code == 403
    assert code(response) == "permission_denied"


def test_old_otp_endpoints_are_gone(world):
    assert APIClient().post("/api/v1/auth/otp/request/", {"mobile": "9000000006"}, format="json").status_code == 404


# --- Console ---------------------------------------------------------------------------------------


def test_console_is_for_super_admins_only(world):
    response = login("da_guntur", client="web_admin")
    assert response.status_code == 403
    assert code(response) == "not_console_user"
    assert "gc_refresh" not in response.cookies
    assert "access" not in response.json()


def test_console_refresh_token_goes_in_an_httponly_cookie(world):
    client = APIClient()
    response = login("super", http=client, client="web_admin")
    assert response.status_code == 200
    assert "refresh" not in response.json()
    cookie = response.cookies["gc_refresh"]
    assert cookie["httponly"] is True and cookie["samesite"] == "Strict" and cookie["path"] == "/api/v1/auth/"
    refreshed = client.post("/api/v1/auth/refresh/", {}, format="json")
    assert refreshed.status_code == 200 and set(refreshed.json()) == {"access"}


def test_console_cookie_refused_from_another_site(world):
    client = APIClient()
    client.post(LOGIN, {"username": "super", "password": TEST_PASSWORD, "client": "web_admin"}, format="json")
    response = client.post("/api/v1/auth/refresh/", {}, format="json", HTTP_ORIGIN="https://evil.example.com")
    assert response.status_code == 403


def test_console_logout_clears_the_cookie(world):
    client = APIClient()
    access = client.post(
        LOGIN, {"username": "super", "password": TEST_PASSWORD, "client": "web_admin"}, format="json"
    ).json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    out = client.post("/api/v1/auth/logout/", {}, format="json")
    assert out.status_code == 204
    assert out.cookies["gc_refresh"].value == ""
    client.credentials()
    assert client.post("/api/v1/auth/refresh/", {}, format="json").status_code == 401


# --- Forced change at first login --------------------------------------------------------------------


@pytest.fixture
def must_change(world):
    user = world.users["va_kolakaluru"]
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    user.refresh_from_db()
    return user


def test_must_change_password_blocks_everything_else(must_change):
    access = login("va_kolakaluru").json()["access"]
    client = bearer(access)
    me = client.get(ME)
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    for method, url in [("get", "/api/v1/users/"), ("get", "/api/v1/review/queue/"), ("patch", ME)]:
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
    assert APIClient().post("/api/v1/auth/refresh/", {"refresh": tablet["refresh"]}, format="json").status_code == 401
    assert login("va_kolakaluru").status_code == 400
    assert login("va_kolakaluru", NEW_PASSWORD).status_code == 200
    must_change.refresh_from_db()
    assert must_change.password_changed_at is not None
    assert AuditLog.objects.filter(action="user.password_changed").exists()


@pytest.mark.parametrize(
    "current, new, error_code, field",
    [
        ("wrong", NEW_PASSWORD, "invalid_credentials", None),
        (TEST_PASSWORD, "12345678", "validation_error", "new_password"),
        (TEST_PASSWORD, "short", "validation_error", "new_password"),
        (TEST_PASSWORD, "password", "validation_error", "new_password"),
        (TEST_PASSWORD, TEST_PASSWORD, "validation_error", "new_password"),
    ],
)
def test_password_change_validation(world, current, new, error_code, field):
    client = bearer(login("va_kolakaluru").json()["access"])
    response = client.post(CHANGE, {"current_password": current, "new_password": new}, format="json")
    assert response.status_code == 400
    assert code(response) == error_code
    if field:
        assert response.json()["error"]["fields"][field]


def test_console_password_change_keeps_the_cookie(world):
    client = APIClient()
    access = client.post(
        LOGIN, {"username": "super", "password": TEST_PASSWORD, "client": "web_admin"}, format="json"
    ).json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = client.post(
        CHANGE, {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "client": "web_admin"}, format="json"
    )
    assert response.status_code == 200
    assert set(response.json()) == {"access"}
    assert response.cookies["gc_refresh"].value


# --- Invites ---------------------------------------------------------------------------------------------


def invite(api, world, actor="va_kolakaluru", **body):
    payload = {
        "name": "Lakshmi",
        "name_te": "లక్ష్మి",
        "email": "Lakshmi.K@Example.org",
        "role": "PANCHAYAT_MEMBER",
        "area": world.area_json("kolakaluru"),
    }
    payload.update(body)
    return api(world.users[actor]).post("/api/v1/users/", payload, format="json")


def test_invite_emails_username_and_role_default_in_telugu(api, world, mailoutbox, django_capture_on_commit_callbacks):
    default = role_default("PANCHAYAT_MEMBER")
    with django_capture_on_commit_callbacks(execute=True):
        response = invite(api, world)
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "lakshmi.k@example.org"
    assert data["email"] == "lakshmi.k@example.org"
    assert data["mobile"] is None
    assert data["must_change_password"] is True

    [mail] = mailoutbox
    assert mail.to == ["lakshmi.k@example.org"]
    assert mail.subject == "మీ BVT Grama Connect లాగిన్ వివరాలు"
    assert "లక్ష్మి" in mail.body and "పంచాయతీ సభ్యుడు" in mail.body and "కొలకలూరు" in mail.body
    assert "lakshmi.k@example.org" in mail.body and default in mail.body

    # The person can log in with it, and must change it first.
    first = login("lakshmi.k@example.org", default)
    assert first.status_code == 200 and first.json()["user"]["must_change_password"] is True

    # The temporary password never reaches the audit log or the job's arguments.
    assert not any(default in str(entry.data) for entry in AuditLog.objects.all())
    from django_q.models import Task

    assert not any(default in str(task.args) for task in Task.objects.all())


def test_invite_email_in_english_with_a_chosen_username(api, world, mailoutbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = invite(api, world, preferred_language="en", username="Lakshmi.Kolakaluru", mobile="9876501234")
    assert response.json()["username"] == "lakshmi.kolakaluru"
    [mail] = mailoutbox
    assert mail.subject == "Your BVT Grama Connect login"
    assert "You have been added to BVT Grama Connect as Panchayat Member for Kolakaluru." in mail.body
    assert "Username: lakshmi.kolakaluru" in mail.body
    assert f"Temporary password: {role_default('PANCHAYAT_MEMBER')}" in mail.body


def test_no_change_required_when_the_setting_is_off(api, world, settings):
    settings.FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN = False
    assert invite(api, world).json()["must_change_password"] is False


def test_email_is_not_sent_if_the_default_changed_meanwhile(api, world, mailoutbox):
    from apps.accounts.tasks import send_credentials_email

    user_id = invite(api, world).json()["id"]
    RoleDefaultPassword.objects.filter(role="PANCHAYAT_MEMBER").update(password="Another-Default-2026")
    assert send_credentials_email(user_id) is False
    assert mailoutbox == []
    entry = AuditLog.objects.get(action="email.skipped")
    assert "Another-Default-2026" not in str(entry.data)


def test_failed_email_is_retried_then_audited_without_the_password(api, world, monkeypatch):
    from apps.accounts import tasks

    user_id = invite(api, world).json()["id"]
    calls = {"n": 0}

    def broken(*args, **kwargs):
        calls["n"] += 1
        raise ConnectionError("smtp down")

    monkeypatch.setattr(tasks, "send_mail", broken)
    assert tasks.send_credentials_email(user_id) is False
    assert calls["n"] == 3
    entry = AuditLog.objects.get(action="email.failed")
    assert entry.data == {"kind": "invite", "to": "la***@example.org", "attempts": 3, "error": "ConnectionError"}
    assert role_default("PANCHAYAT_MEMBER") not in str(entry.data)


# --- Reset password -------------------------------------------------------------------------------


@pytest.mark.parametrize("actor, status", [("va_kolakaluru", 200), ("ma_tenali", 200), ("va_nandivelugu", 404), ("pm_nandivelugu", 403)])
def test_reset_password_follows_the_management_rules(api, world, mailoutbox, django_capture_on_commit_callbacks, actor, status):
    target = world.users["pm_kolakaluru"]
    old_session = issue_tokens(target)
    with django_capture_on_commit_callbacks(execute=True):
        response = api(world.users[actor]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json")
    assert response.status_code == status
    target.refresh_from_db()
    if status == 200:
        assert target.must_change_password is True
        assert target.check_password(role_default("PANCHAYAT_MEMBER"))
        assert code(bearer(old_session["access"]).get(ME)) == "session_revoked"
        [mail] = mailoutbox
        assert mail.subject == "మీ BVT Grama Connect పాస్‌వర్డ్ రీసెట్ అయింది"
        assert AuditLog.objects.filter(action="user.password_reset").exists()
    else:
        assert target.check_password(TEST_PASSWORD)
        assert mailoutbox == []


# --- Role default passwords -------------------------------------------------------------------------


def test_super_admin_sees_a_random_default_for_every_role(api, world):
    rows = api(world.users["super"]).get("/api/v1/admin/role-passwords/").json()
    assert [row["role"] for row in rows] == [
        "SUPER_ADMIN", "DISTRICT_ADMIN", "MANDAL_ADMIN", "VILLAGE_ADMIN", "PANCHAYAT_MEMBER",
    ]
    passwords = [row["password"] for row in rows]
    assert len(set(passwords)) == 5 and all(len(p) >= 12 for p in passwords)


@pytest.mark.parametrize("name", ["da_guntur", "va_kolakaluru", "pm_kolakaluru"])
def test_only_super_admins_see_or_change_defaults(api, world, name):
    client = api(world.users[name])
    assert code(client.get("/api/v1/admin/role-passwords/")) == "permission_denied"
    response = client.patch("/api/v1/admin/role-passwords/PANCHAYAT_MEMBER/", {"password": "Village-Default-2026"}, format="json")
    assert response.status_code == 403


def test_super_admin_changes_a_default_for_future_invites(api, world, mailoutbox, django_capture_on_commit_callbacks):
    client = api(world.users["super"])
    response = client.patch("/api/v1/admin/role-passwords/PANCHAYAT_MEMBER/", {"password": "Village-Default-2026"}, format="json")
    assert response.status_code == 200
    assert response.json()["password"] == "Village-Default-2026"
    assert response.json()["updated_by"]["name"] == "super"
    entry = AuditLog.objects.get(action="setting.role_password_changed")
    assert entry.type == "settings" and "Village-Default-2026" not in str(entry.data)

    with django_capture_on_commit_callbacks(execute=True):
        invite(api, world)
    assert "Village-Default-2026" in mailoutbox[0].body


@pytest.mark.parametrize("password", ["short", "12345678", "password1"])
def test_weak_defaults_are_refused(api, world, password):
    response = api(world.users["super"]).patch(
        "/api/v1/admin/role-passwords/MANDAL_ADMIN/", {"password": password}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["error"]["fields"]["password"]


def test_unknown_role_is_404(api, world):
    response = api(world.users["super"]).patch("/api/v1/admin/role-passwords/KING/", {"password": "Whatever-2026-x"}, format="json")
    assert response.status_code == 404

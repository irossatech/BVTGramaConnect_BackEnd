"""Password length: at least 12 characters for every official, Panchayat members included (decision of
2026-11-19; before, security gap G12 of 2026-11-03 asked 12 of admins and 8 of Panchayat members).
Expected lengths come from that rule here, not from the code under test."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.credentials import generate_password
from apps.accounts.models import RoleGroup, User
from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD, assign

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
CHANGE = "/api/v1/auth/password/change/"
#: The rule, written out: the same 12 for every role.
EXPECTED = {
    Role.SUPER_ADMIN: 12,
    Role.DISTRICT_ADMIN: 12,
    Role.MANDAL_ADMIN: 12,
    Role.VILLAGE_ADMIN: 12,
    Role.PANCHAYAT_MEMBER: 12,
}
ELEVEN = "Gram-Sabha1"     # 11 characters, passes every other rule
TWELVE = "Gram-Sabha12"    # 12
NINE = "Gram-Sab1"         # 9: enough for members before 2026-11-19, not now


def code(response):
    return response.json()["error"]["code"]


def create(api, world, actor, role, area, password, username="new.official"):
    body = {"name": "New Official", "username": username, "role": role, "area": world.area_json(area),
            "password": password}
    return api(world.users[actor]).post("/api/v1/users/", body, format="json")


def test_every_role_group_holds_the_same_length(world):
    assert {row.key: row.password_min_length for row in RoleGroup.objects.all()} == EXPECTED
    # Generated temporary passwords must satisfy it.
    assert len(generate_password()) >= max(EXPECTED.values())


@pytest.mark.parametrize(
    "actor, role, area",
    [
        ("va_kolakaluru", Role.PANCHAYAT_MEMBER, "kolakaluru"),
        ("ma_tenali", Role.VILLAGE_ADMIN, "kolakaluru"),
        ("da_guntur", Role.MANDAL_ADMIN, "tenali"),
        ("super", Role.DISTRICT_ADMIN, "guntur"),
    ],
)
def test_a_new_accounts_chosen_password_needs_twelve(api, world, actor, role, area):
    refused = create(api, world, actor, role, area, ELEVEN)
    assert refused.status_code == 400 and "password" in refused.json()["error"]["fields"]
    assert not User.objects.filter(username="new.official").exists()
    assert create(api, world, actor, role, area, TWELVE).status_code == 201


def test_a_panchayat_member_needs_twelve_too(api, world):
    for short in (NINE, ELEVEN):
        response = create(api, world, "va_kolakaluru", Role.PANCHAYAT_MEMBER, "kolakaluru", short)
        assert response.status_code == 400 and "12" in response.json()["error"]["message"]
        # The message is for everyone now, not only "Admin accounts".
        assert "Admin" not in response.json()["error"]["message"]


@pytest.mark.parametrize("target, actor", [("pm_kolakaluru", "va_kolakaluru"), ("va_kolakaluru", "ma_tenali")])
def test_a_reset_needs_twelve_for_every_role(api, world, target, actor):
    url = f"/api/v1/users/{world.users[target].id}/reset-password/"
    assert api(world.users[actor]).post(url, {"password": ELEVEN}, format="json").status_code == 400
    assert api(world.users[actor]).post(url, {"password": TWELVE}, format="json").status_code == 200


def test_a_generated_reset_always_fits(api, world):
    url = f"/api/v1/users/{world.users['pm_kolakaluru'].id}/reset-password/"
    response = api(world.users["va_kolakaluru"]).post(url, {}, format="json")
    assert response.status_code == 200 and len(response.json()["temporary_password"]) >= 12


@pytest.mark.parametrize("name, new, expected", [
    ("pm_kolakaluru", NINE, 400),
    ("pm_kolakaluru", ELEVEN, 400),
    ("pm_kolakaluru", TWELVE, 200),
    ("va_kolakaluru", ELEVEN, 400),
    ("va_kolakaluru", TWELVE, 200),
    ("super", ELEVEN, 400),
])
def test_changing_your_own_password(api, world, name, new, expected):
    response = api(world.users[name]).post(
        CHANGE, {"current_password": TEST_PASSWORD, "new_password": new}, format="json"
    )
    assert response.status_code == expected
    if expected == 400:
        assert code(response) == "validation_error" and "new_password" in response.json()["error"]["fields"]


def test_someone_with_a_member_and_an_admin_role_needs_twelve(api, world):
    assign(world, "pm_kolakaluru", Role.VILLAGE_ADMIN, "nandivelugu")
    response = api(world.users["pm_kolakaluru"]).post(
        CHANGE, {"current_password": TEST_PASSWORD, "new_password": ELEVEN}, format="json"
    )
    assert response.status_code == 400


# --- Passwords set before the rule ----------------------------------------------------------------


def login(username, password):
    return APIClient().post(LOGIN, {"username": username, "password": password}, format="json")


@pytest.mark.parametrize("name", ["va_kolakaluru", "pm_kolakaluru"])
def test_a_short_existing_password_must_be_changed_at_login(world, name):
    """An admin or a Panchayat member with a password shorter than 12 (a member's 8 to 11 from before
    2026-11-19) still logs in, then must choose a new one before anything else. Nobody is locked out."""
    user = world.users[name]
    user.set_password(NINE)
    user.save()
    response = login(user.username, NINE)
    assert response.status_code == 200
    assert response.json()["user"]["must_change_password"] is True
    assert User.objects.get(pk=user.pk).must_change_password is True
    entry = AuditLog.objects.get(action="auth.password_too_short")
    assert entry.target_id == str(user.pk) and NINE not in str(entry.data)


def test_a_long_enough_password_logs_in_as_before(world):
    for name in ("va_kolakaluru", "pm_kolakaluru"):
        assert login(name, TEST_PASSWORD).json()["user"]["must_change_password"] is False

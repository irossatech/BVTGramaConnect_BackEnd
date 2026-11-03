"""Security gap G12 (owner decision of 2026-11-03): admins need passwords of at least 12 characters,
Panchayat members at least 8. Expected lengths come from that rule here, not from the code under test."""

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
#: The owner's rule, written out.
EXPECTED = {
    Role.SUPER_ADMIN: 12,
    Role.DISTRICT_ADMIN: 12,
    Role.MANDAL_ADMIN: 12,
    Role.VILLAGE_ADMIN: 12,
    Role.PANCHAYAT_MEMBER: 8,
}
ELEVEN = "Gram-Sabha1"     # 11 characters, passes every other rule
TWELVE = "Gram-Sabha12"    # 12
NINE = "Gram-Sab1"         # 9


def code(response):
    return response.json()["error"]["code"]


def create(api, world, actor, role, area, password, username="new.official"):
    body = {"name": "New Official", "username": username, "role": role, "area": world.area_json(area),
            "password": password}
    return api(world.users[actor]).post("/api/v1/users/", body, format="json")


def test_every_role_group_holds_the_owners_length(world):
    assert {row.key: row.password_min_length for row in RoleGroup.objects.all()} == EXPECTED
    # Generated temporary passwords must satisfy the longest one.
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
def test_a_new_accounts_chosen_password_follows_its_role(api, world, actor, role, area):
    short = ELEVEN if EXPECTED[role] == 12 else "Gra-Sa1"  # 7: below even the members' 8
    refused = create(api, world, actor, role, area, short)
    assert refused.status_code == 400 and "password" in refused.json()["error"]["fields"]
    assert not User.objects.filter(username="new.official").exists()
    assert create(api, world, actor, role, area, TWELVE if EXPECTED[role] == 12 else NINE).status_code == 201


def test_a_member_may_keep_a_short_password_an_admin_may_not(api, world):
    assert create(api, world, "va_kolakaluru", Role.PANCHAYAT_MEMBER, "kolakaluru", NINE).status_code == 201
    response = create(api, world, "ma_tenali", Role.VILLAGE_ADMIN, "kolakaluru", ELEVEN, username="new.admin")
    assert response.status_code == 400 and "12" in response.json()["error"]["message"]


@pytest.mark.parametrize("target, actor", [("pm_kolakaluru", "va_kolakaluru"), ("va_kolakaluru", "ma_tenali")])
def test_a_reset_follows_the_targets_role(api, world, target, actor):
    url = f"/api/v1/users/{world.users[target].id}/reset-password/"
    response = api(world.users[actor]).post(url, {"password": NINE}, format="json")
    expected_ok = EXPECTED[Role.PANCHAYAT_MEMBER if target.startswith("pm_") else Role.VILLAGE_ADMIN] <= len(NINE)
    assert response.status_code == (200 if expected_ok else 400)


def test_a_generated_reset_always_fits(api, world):
    url = f"/api/v1/users/{world.users['va_kolakaluru'].id}/reset-password/"
    response = api(world.users["ma_tenali"]).post(url, {}, format="json")
    assert response.status_code == 200 and len(response.json()["temporary_password"]) >= 12


@pytest.mark.parametrize("name, new, expected", [
    ("pm_kolakaluru", NINE, 200),
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


def test_someone_with_a_member_and_an_admin_role_needs_the_longer(api, world):
    assign(world, "pm_kolakaluru", Role.VILLAGE_ADMIN, "nandivelugu")
    response = api(world.users["pm_kolakaluru"]).post(
        CHANGE, {"current_password": TEST_PASSWORD, "new_password": ELEVEN}, format="json"
    )
    assert response.status_code == 400


# --- Passwords set before the rule, or a role given later ----------------------------------------


def login(username, password):
    return APIClient().post(LOGIN, {"username": username, "password": password}, format="json")


def test_an_admin_with_a_short_password_must_change_it_at_login(world):
    user = world.users["va_kolakaluru"]
    user.set_password(NINE)
    user.save()
    response = login(user.username, NINE)
    assert response.status_code == 200
    assert response.json()["user"]["must_change_password"] is True
    assert User.objects.get(pk=user.pk).must_change_password is True
    entry = AuditLog.objects.get(action="auth.password_too_short")
    assert entry.target_id == str(user.pk) and NINE not in str(entry.data)


def test_a_member_with_the_same_password_is_not_asked(world):
    user = world.users["pm_kolakaluru"]
    user.set_password(NINE)
    user.save()
    assert login(user.username, NINE).json()["user"]["must_change_password"] is False
    assert not AuditLog.objects.filter(action="auth.password_too_short").exists()


def test_a_member_given_an_admin_role_is_asked_at_the_next_login(world):
    user = world.users["pm_kolakaluru"]
    user.set_password(NINE)
    user.save()
    assign(world, "pm_kolakaluru", Role.VILLAGE_ADMIN, "nandivelugu")
    assert login(user.username, NINE).json()["user"]["must_change_password"] is True


def test_a_long_enough_password_logs_in_as_before(world):
    assert login("va_kolakaluru", TEST_PASSWORD).json()["user"]["must_change_password"] is False

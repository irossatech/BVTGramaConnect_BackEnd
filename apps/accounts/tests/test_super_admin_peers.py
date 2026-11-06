"""A Super Admin creates another Super Admin (super-admin plan of 2026-11-06).

Expected answers come from the plan: S1 the actor's own password, S2 never yourself, S3 never zero
active Super Admins, S4 at most MAX_SUPER_ADMINS, S6 the audit, S8 peers act on each other.
The notification to the other Super Admins (S7) is in apps/notifications/tests/test_super_admin_added.py.
"""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts import authz, peers
from apps.accounts.models import RoleAssignment, User
from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from conftest import TEST_PASSWORD, make_official

pytestmark = pytest.mark.django_db

USERS = "/api/v1/users/"


def new_super_admin(api, world, actor="super", username="ravi.admin", **extra):
    body = {"name": "Ravi Kumar", "name_te": "రవి కుమార్", "username": username, "role": Role.SUPER_ADMIN,
            "area": world.area_json("ap"), "current_password": TEST_PASSWORD, **extra}
    return api(world.users[actor]).post(USERS, body, format="json")


def code(response):
    return response.json()["error"]["code"]


def second_super_admin(world, name="super2"):
    world.users[name] = make_official(world, name, Role.SUPER_ADMIN, "ap", "9111100002")
    return world.users[name]


def super_admin_role():
    return authz.roles()[Role.SUPER_ADMIN]


# --- 1. Creating one ------------------------------------------------------------------------------


def test_a_super_admin_creates_a_super_admin_with_their_own_password(api, world):
    response = new_super_admin(api, world)
    assert response.status_code == 201, response.content
    user = User.objects.get(username="ravi.admin")
    assignment = user.role_assignments.get()
    assert (assignment.role, assignment.area_level, assignment.is_active) == (Role.SUPER_ADMIN, "STATE", True)
    assert user.must_change_password
    assert user.created_by == world.users["super"]
    assert response.json()["temporary_password"]  # generated, shown once
    # Console access and the new permission come with the group.
    user.must_change_password = False
    assert authz.can(user, "web_admin.access_web_console", authz.PLATFORM)
    assert authz.can(user, "accounts.manage_super_admin")


def test_me_lists_the_new_permission_only_for_super_admins(api, world):
    assert "accounts.manage_super_admin" in api(world.users["super"]).get("/api/v1/me/").json()["permissions"]
    for name in ("da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"):
        assert "accounts.manage_super_admin" not in api(world.users[name]).get("/api/v1/me/").json()["permissions"]


# --- S1: the actor's own password --------------------------------------------------------------------


def test_a_missing_password_is_a_validation_error(api, world):
    response = new_super_admin(api, world, current_password="")
    assert response.status_code == 400
    assert code(response) == "validation_error"
    assert "current_password" in response.json()["error"]["fields"]
    assert not User.objects.filter(username="ravi.admin").exists()


def test_a_wrong_password_is_refused_and_counted(api, world):
    response = new_super_admin(api, world, current_password="Not-My-Password-1")
    assert response.status_code == 403
    assert code(response) == "password_confirmation_required"
    assert response.json()["error"]["message"] == "Your password is not correct."
    assert not User.objects.filter(username="ravi.admin").exists()
    failed = AuditLog.objects.get(action="auth.password_change_failed")
    assert failed.data == {"for": "give_super_admin"}
    assert "Not-My-Password" not in str(failed.data)


def test_too_many_wrong_passwords_end_every_session(api, world, settings):
    settings.PASSWORD_CHANGE_MAX_FAILURES = 3
    client = api(world.users["super"])
    body = {"name": "Ravi", "username": "ravi.admin", "role": Role.SUPER_ADMIN, "area": world.area_json("ap"),
            "current_password": "Not-My-Password-1"}
    answers = [client.post(USERS, body, format="json") for _ in range(3)]
    assert [r.status_code for r in answers] == [403, 403, 401]
    assert code(answers[-1]) == "session_revoked"
    assert AuditLog.objects.filter(action="auth.password_change_locked").count() == 1


def test_other_roles_need_no_password(api, world):
    body = {"name": "Lakshmi", "username": "lakshmi", "role": Role.DISTRICT_ADMIN, "area": world.area_json("krishna")}
    assert api(world.users["super"]).post(USERS, body, format="json").status_code == 201


# --- Only Super Admins, and only Super Admins have peers -------------------------------------------


@pytest.mark.parametrize("actor", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_nobody_else_gives_the_super_admin_role(api, world, actor):
    response = new_super_admin(api, world, actor=actor)
    assert response.status_code == 403
    assert code(response) == "permission_denied"
    target = world.users["pm_kolakaluru"]
    added = api(world.users[actor]).post(
        f"{USERS}{target.id}/role-assignments/",
        {"role": Role.SUPER_ADMIN, "area": world.area_json("ap"), "current_password": TEST_PASSWORD}, format="json",
    )
    assert added.status_code in (403, 404)
    assert not RoleAssignment.objects.of_role(Role.SUPER_ADMIN).exclude(user=world.users["super"]).exists()


def test_a_district_admin_still_cannot_make_a_district_admin(api, world):
    body = {"name": "Peer", "username": "peer", "role": Role.DISTRICT_ADMIN, "area": world.area_json("guntur"),
            "current_password": TEST_PASSWORD}
    response = api(world.users["da_guntur"]).post(USERS, body, format="json")
    assert response.status_code == 403
    assert code(response) == "permission_denied"


def test_the_role_goes_only_at_the_state(api, world):
    response = new_super_admin(api, world, area=world.area_json("guntur"))
    assert response.status_code == 400
    assert code(response) == "validation_error"


# --- Adding the role to an existing official ----------------------------------------------------------


def test_adding_the_role_needs_the_password_and_is_audited(api, world):
    target = world.users["da_guntur"]
    url = f"{USERS}{target.id}/role-assignments/"
    client = api(world.users["super"])
    missing = client.post(url, {"role": Role.SUPER_ADMIN, "area": world.area_json("ap")}, format="json")
    assert missing.status_code == 400
    wrong = client.post(url, {"role": Role.SUPER_ADMIN, "area": world.area_json("ap"),
                              "current_password": "Not-My-Password-1"}, format="json")
    assert wrong.status_code == 403 and code(wrong) == "password_confirmation_required"
    added = client.post(url, {"role": Role.SUPER_ADMIN, "area": world.area_json("ap"),
                              "current_password": TEST_PASSWORD}, format="json")
    assert added.status_code == 201, added.content
    assert RoleAssignment.objects.of_role(Role.SUPER_ADMIN).filter(user=target, is_active=True).exists()
    entry = AuditLog.objects.get(action="user.super_admin_role_added")
    assert entry.actor == world.users["super"] and entry.target_id == str(target.pk)
    assert TEST_PASSWORD not in str(entry.data)


# --- S2, S8: peers act on each other, never on themselves ------------------------------------------


def test_a_super_admin_blocks_unblocks_and_resets_another(api, world):
    other = second_super_admin(world)
    client = api(world.users["super"])
    listed = {row["id"] for row in client.get(f"{USERS}?page_size=100").json()["results"]}
    assert str(other.id) in listed
    assert set(client.get(f"{USERS}{other.id}/").json()["allowed_actions"]) >= {"block", "reset_password"}
    assert client.post(f"{USERS}{other.id}/block/", {}, format="json").status_code == 200
    assert client.post(f"{USERS}{other.id}/unblock/", {}, format="json").status_code == 200
    reset = client.post(f"{USERS}{other.id}/reset-password/", {}, format="json")
    assert reset.status_code == 200
    other.refresh_from_db()
    assert other.must_change_password


def test_a_super_admin_never_acts_on_themselves(api, world):
    me = world.users["super"]
    client = api(me)
    for action in ("block", "reset-password", "unlock"):
        assert client.post(f"{USERS}{me.id}/{action}/", {}, format="json").status_code == 404
    assert str(me.id) not in {row["id"] for row in client.get(f"{USERS}?page_size=100").json()["results"]}


def test_lower_admins_still_cannot_touch_a_super_admin(api, world):
    for name in ("da_guntur", "ma_tenali", "va_kolakaluru"):
        response = api(world.users[name]).post(f"{USERS}{world.users['super'].id}/block/", {}, format="json")
        assert response.status_code == 404


# --- S3: never zero -----------------------------------------------------------------------------------


def test_the_last_active_super_admin_cannot_be_blocked_or_lose_the_role(world):
    """The API never lets a Super Admin act on themselves, so the guard is checked directly too."""
    role, only = super_admin_role(), world.users["super"]
    with pytest.raises(Exception) as refused:
        peers.check_not_last(role, only)
    assert refused.value.error_code == "last_super_admin"
    assert refused.value.status_code == 409
    second_super_admin(world)
    peers.check_not_last(role, only)  # another one is left: allowed


def test_blocking_another_always_leaves_the_actor(api, world):
    other = second_super_admin(world)
    client = api(world.users["super"])
    assert client.post(f"{USERS}{other.id}/block/", {}, format="json").status_code == 200
    assert list(peers.active_holders(super_admin_role())) == [world.users["super"]]


def test_removing_or_switching_off_the_role_is_audited(api, world):
    other = second_super_admin(world)
    client = api(world.users["super"])
    assignment = other.role_assignments.get()
    url = f"{USERS}{other.id}/role-assignments/{assignment.id}/"
    assert client.patch(url, {"is_active": False}, format="json").status_code == 200
    assert AuditLog.objects.filter(action="user.super_admin_role_removed").count() == 1
    assert client.patch(url, {"is_active": True}, format="json").status_code == 200
    assert AuditLog.objects.filter(action="user.super_admin_role_added").count() == 1
    assert client.delete(url).status_code == 204
    assert AuditLog.objects.filter(action="user.super_admin_role_removed").count() == 2


# --- S4: the limit -------------------------------------------------------------------------------------


def test_the_limit_of_active_super_admins(api, world, settings):
    settings.MAX_SUPER_ADMINS = 5
    for index in range(4):  # with "super": 5 active
        assert new_super_admin(api, world, username=f"admin{index}").status_code == 201
    sixth = new_super_admin(api, world, username="admin5")
    assert sixth.status_code == 409
    assert code(sixth) == "super_admin_limit"
    assert response_limit(sixth) == 5
    assert not User.objects.filter(username="admin5").exists()
    # A blocked one does not count: block one, then there is room again.
    blocked = User.objects.get(username="admin0")
    assert api(world.users["super"]).post(f"{USERS}{blocked.id}/block/", {}, format="json").status_code == 200
    assert new_super_admin(api, world, username="admin5").status_code == 201
    # ... and unblocking it now would make six.
    unblock = api(world.users["super"]).post(f"{USERS}{blocked.id}/unblock/", {}, format="json")
    assert unblock.status_code == 409 and code(unblock) == "super_admin_limit"


def response_limit(response):
    return response.json()["error"]["limit"]


def test_zero_means_no_limit(api, world, settings):
    settings.MAX_SUPER_ADMINS = 0
    for index in range(6):
        assert new_super_admin(api, world, username=f"admin{index}").status_code == 201


def test_a_negative_limit_is_a_configuration_error(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    from grama.settings import base

    monkeypatch.setenv("MAX_SUPER_ADMINS", "-1")
    try:
        with pytest.raises(ImproperlyConfigured):
            importlib.reload(base)
    finally:
        monkeypatch.delenv("MAX_SUPER_ADMINS")
        importlib.reload(base)


# --- S6: audit ------------------------------------------------------------------------------------------


def test_creating_is_audited_without_any_password(api, world):
    response = new_super_admin(api, world, password="Temp-Pass-2026x")
    assert response.status_code == 201, response.content
    entry = AuditLog.objects.get(action="user.super_admin_created")
    assert entry.actor == world.users["super"]
    assert entry.data == {"role": Role.SUPER_ADMIN, "password": "set by admin"}
    everything = str(list(AuditLog.objects.values_list("data", flat=True)))
    for secret in (TEST_PASSWORD, "Temp-Pass-2026x"):
        assert secret not in everything


# --- 9. The first one is still made on the server -------------------------------------------------------


def test_create_super_admin_still_makes_the_first_one(geo):
    out = StringIO()
    call_command("create_super_admin", "--username", "sita.admin", "--name", "Sita", stdout=out)
    user = User.objects.get(username="sita.admin")
    assert user.must_change_password
    assert RoleAssignment.objects.of_role(Role.SUPER_ADMIN).filter(user=user, is_active=True).exists()

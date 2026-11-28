"""S3 "never zero" and S4 "at most MAX_SUPER_ADMINS" hold even when two changes pass the early check at the
same moment (audit finding BE-DB-004). The early checks are switched off here to stand for the other,
concurrent request; the check inside the transaction must still refuse and undo the whole change.
"""

from unittest import mock

import pytest
from django.db import transaction

from apps.accounts import authz, peers
from apps.accounts.models import RoleAssignment, User
from apps.accounts.roles import Role
from conftest import TEST_PASSWORD, make_official

pytestmark = pytest.mark.django_db
USERS = "/api/v1/users/"


def role():
    return authz.roles()[Role.SUPER_ADMIN]


def test_two_grants_at_once_cannot_pass_the_limit(api, world, settings):
    settings.MAX_SUPER_ADMINS = 1  # "super" is the one
    body = {"name": "Ravi", "username": "ravi.admin", "role": Role.SUPER_ADMIN, "area": world.area_json("ap"),
            "current_password": TEST_PASSWORD}
    with mock.patch.object(peers, "check_room_for_one_more"):  # the early check saw room
        response = api(world.users["super"]).post(USERS, body, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "super_admin_limit"
    assert not User.objects.filter(username="ravi.admin").exists()  # rolled back


def test_a_second_role_cannot_pass_the_limit_either(api, world, settings):
    settings.MAX_SUPER_ADMINS = 1
    target = world.users["da_guntur"]
    body = {"role": Role.SUPER_ADMIN, "area": world.area_json("ap"), "current_password": TEST_PASSWORD}
    with mock.patch.object(peers, "check_room_for_one_more"):
        response = api(world.users["super"]).post(f"{USERS}{target.pk}/role-assignments/", body, format="json")
    assert response.status_code == 409
    assert not RoleAssignment.objects.filter(user=target, group__role_group__key=Role.SUPER_ADMIN).exists()


def test_the_last_super_admin_cannot_be_taken_away(world):
    only = world.users["super"]
    assignment = only.role_assignments.get()
    with pytest.raises(Exception) as refused:
        with transaction.atomic():
            peers.lock(role())
            RoleAssignment.objects.filter(pk=assignment.pk).update(is_active=False)
            peers.confirm(role(), adding=False)
    assert refused.value.error_code == "last_super_admin"
    assignment.refresh_from_db()
    assert assignment.is_active  # rolled back


def test_blocking_each_other_leaves_one(api, world):
    """super2 blocked super a moment ago (committed); super's own block of super2 then must fail."""
    second = make_official(world, "super2", Role.SUPER_ADMIN, "ap", "9111100002")
    client = api(world.users["super"])  # super's request was let in before super2's block landed
    User.objects.filter(pk=world.users["super"].pk).update(is_active=False)
    with mock.patch.object(peers, "check_not_last"):  # the early check saw two
        with mock.patch("apps.accounts.authentication.GramaJWTAuthentication.get_user", return_value=world.users["super"]):
            response = client.post(f"{USERS}{second.pk}/block/", {"reason": "x"}, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "last_super_admin"
    second.refresh_from_db()
    assert second.is_active  # rolled back: one Super Admin is left


def test_ordinary_roles_are_not_locked_or_rechecked(api, world):
    with mock.patch.object(peers, "confirm", wraps=peers.confirm) as confirm:
        response = api(world.users["da_guntur"]).post(f"{USERS}{world.users['pm_kolakaluru'].pk}/block/", {"reason": "x"}, format="json")
    assert response.status_code == 200
    assert confirm.call_count == 0  # a Panchayat member holds no peer role

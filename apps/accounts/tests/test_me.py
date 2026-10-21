"""GET and PATCH /me: the user, their groups, assignments, effective permissions and rules."""

import pytest

from apps.accounts.models import RoleAssignment
from apps.accounts.roles import Role
from conftest import assign

pytestmark = pytest.mark.django_db


def test_me_describes_groups_assignments_permissions_and_rules(api, world):
    data = api(world.users["ma_tenali"]).get("/api/v1/me/").json()
    assert data["username"] == "ma_tenali"
    assert data["mobile"] == world.users["ma_tenali"].mobile
    assert data["is_active"] is True and data["status"] == "ACTIVE"
    assert data["must_change_password"] is False
    assert data["preferred_language"] == "te"
    assert data["groups"] == [{"key": "MANDAL_ADMIN", "name": "Mandal Admin", "level": "MANDAL", "rank": 2}]
    tenali = {"level": "MANDAL", "id": world.areas["tenali"].id, "name": "తెనాలి", "path": "తెనాలి · గుంటూరు"}
    [assignment] = data["assignments"]
    assert assignment["group"] == {"key": "MANDAL_ADMIN", "name": "Mandal Admin"}
    assert assignment["area"] == tenali and assignment["is_active"] is True
    assert "posts.publish_post" in data["permissions"] and "accounts.manage_village_admin" in data["permissions"]
    assert "accounts.manage_mandal_admin" not in data["permissions"]
    assert "web_admin.access_web_console" not in data["permissions"]
    assert data["permissions_by_area"] == [{"area": tenali, "permissions": data["permissions"]}]
    # Kept for older app builds; derived from the permissions.
    assert data["role_assignments"][0]["role"] == "MANDAL_ADMIN"
    assert data["capabilities"] == {
        "can_post": True,
        "can_review": True,
        "can_manage_users": True,
        "manageable_roles": ["VILLAGE_ADMIN", "PANCHAYAT_MEMBER"],
        "is_super_admin": False,
    }
    assert data["rules"] == {
        "admin_posts_auto_publish": True,
        "super_admin_can_post": False,
        "max_photos_per_post": 5,
        "video_enabled": True,  # video posts are in version 1 (owner decision Q8)
    }


def test_super_admin_sees_the_console_permission(api, world):
    data = api(world.users["super"]).get("/api/v1/me/").json()
    assert "web_admin.access_web_console" in data["permissions"]
    assert "posts.create_post" not in data["permissions"]  # SUPER_ADMIN_CAN_POST is off
    assert data["capabilities"]["is_super_admin"] is True


def test_two_assignments_are_listed_per_area(api, world):
    assign(world, "ma_tenali", Role.VILLAGE_ADMIN, "kaza")
    data = api(world.users["ma_tenali"]).get("/api/v1/me/").json()
    assert [g["key"] for g in data["groups"]] == ["MANDAL_ADMIN", "VILLAGE_ADMIN"]
    by_area = {row["area"]["id"]: row["permissions"] for row in data["permissions_by_area"]}
    assert "accounts.manage_village_admin" in by_area[world.areas["tenali"].id]
    assert "accounts.manage_village_admin" not in by_area[world.areas["kaza"].id]


def test_inactive_assignments_are_shown_but_grant_nothing(api, world):
    RoleAssignment.objects.filter(user=world.users["pm_kolakaluru"]).update(is_active=False)
    assign(world, "pm_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")
    data = api(world.users["pm_kolakaluru"]).get("/api/v1/me/").json()
    assert [(row["area"]["id"], row["is_active"]) for row in data["assignments"]] == [
        (world.areas["kolakaluru"].id, False),
        (world.areas["kaza"].id, True),
    ]
    assert [row["area"]["id"] for row in data["permissions_by_area"]] == [world.areas["kaza"].id]


def test_area_names_follow_accept_language(api, world):
    data = api(world.users["ma_tenali"]).get("/api/v1/me/", HTTP_ACCEPT_LANGUAGE="en").json()
    assert data["assignments"][0]["area"]["path"] == "Tenali · Guntur"


def test_change_own_name_and_language(api, world):
    client = api(world.users["pm_kolakaluru"])
    response = client.patch("/api/v1/me/", {"name_te": "రవి కుమార్", "preferred_language": "en"}, format="json")
    assert response.status_code == 200
    assert response.json()["name_te"] == "రవి కుమార్"
    assert response.json()["preferred_language"] == "en"


def test_cannot_change_own_status_mobile_or_groups(api, world):
    user = world.users["pm_kolakaluru"]
    api(user).patch(
        "/api/v1/me/", {"is_active": False, "mobile": "9999999999", "groups": [1], "must_change_password": True},
        format="json",
    )
    user.refresh_from_db()
    assert user.is_active and not user.must_change_password
    assert user.mobile != "9999999999"
    assert list(user.groups.values_list("role_group__key", flat=True)) == ["PANCHAYAT_MEMBER"]

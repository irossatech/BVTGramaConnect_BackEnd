"""GET and PATCH /me."""

import pytest

pytestmark = pytest.mark.django_db


def test_me_describes_roles_capabilities_and_rules(api, world):
    data = api(world.users["ma_tenali"]).get("/api/v1/me/").json()
    assert data["mobile"] == world.users["ma_tenali"].mobile
    assert data["status"] == "ACTIVE"
    assert data["preferred_language"] == "te"
    [assignment] = data["role_assignments"]
    assert assignment["role"] == "MANDAL_ADMIN"
    assert assignment["area"] == {
        "level": "MANDAL",
        "id": world.areas["tenali"].id,
        "name": "తెనాలి",
        "path": "తెనాలి · గుంటూరు",
    }
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


def test_area_names_follow_accept_language(api, world):
    data = api(world.users["ma_tenali"]).get("/api/v1/me/", HTTP_ACCEPT_LANGUAGE="en").json()
    assert data["role_assignments"][0]["area"]["path"] == "Tenali · Guntur"


def test_change_own_name_and_language(api, world):
    client = api(world.users["pm_kolakaluru"])
    response = client.patch("/api/v1/me/", {"name_te": "రవి కుమార్", "preferred_language": "en"}, format="json")
    assert response.status_code == 200
    assert response.json()["name_te"] == "రవి కుమార్"
    assert response.json()["preferred_language"] == "en"


def test_cannot_change_own_status_or_mobile(api, world):
    user = world.users["pm_kolakaluru"]
    api(user).patch("/api/v1/me/", {"status": "BLOCKED", "mobile": "9999999999"}, format="json")
    user.refresh_from_db()
    assert user.status == "ACTIVE"
    assert user.mobile != "9999999999"

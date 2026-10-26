"""Who may add, read, edit and remove important numbers where (contacts plan, section 6).

Worked out here from the rules, not from the code: every official holds
contacts.manage_contact in their own area and below; only the Super Admin
covers the STATE, so only they touch statewide helplines; anything outside
reach answers 404, and adding or moving into it answers 403 out_of_area.
"""

import pytest

from apps.contacts.models import Contact
from conftest import inside

from .conftest import ADMIN_URL, area_body, helpline_body

pytestmark = pytest.mark.django_db

ACTORS = {  # name: (home area, platform-wide)
    "super": ("ap", True),
    "da_guntur": ("guntur", False),
    "ma_tenali": ("tenali", False),
    "va_kolakaluru": ("kolakaluru", False),
    "pm_kolakaluru": ("kolakaluru", False),
}
AREAS = ["guntur", "krishna", "tenali", "mangalagiri", "kolakaluru", "nandivelugu", "nidamanuru"]


def reaches(actor: str, area_key: str) -> bool:
    home, platform_wide = ACTORS[actor]
    if area_key == "ap":
        return platform_wide
    return platform_wide or inside(area_key, home)


def body_for(world, area_key: str) -> dict:
    return helpline_body(world) if area_key == "ap" else area_body(world, area_key)


@pytest.mark.parametrize("actor", list(ACTORS))
@pytest.mark.parametrize("area_key", ["ap", *AREAS])
def test_create(world, api, actor, area_key):
    response = api(world.users[actor]).post(ADMIN_URL, body_for(world, area_key), format="json")
    if reaches(actor, area_key):
        assert response.status_code == 201, response.json()
        assert Contact.objects.count() == 1
    else:
        assert response.status_code == 403, response.json()
        assert response.json()["error"]["code"] == "out_of_area"
        assert Contact.objects.count() == 0


@pytest.mark.parametrize("actor", list(ACTORS))
@pytest.mark.parametrize("area_key", AREAS)
def test_read_edit_remove_area_numbers(world, api, number, actor, area_key):
    contact = number(area_key)
    client, url = api(world.users[actor]), f"{ADMIN_URL}{contact.id}/"
    allowed = reaches(actor, area_key)
    assert client.get(url).status_code == (200 if allowed else 404)
    assert client.patch(url, {"name": "Renamed"}, format="json").status_code == (200 if allowed else 404)
    assert client.delete(url).status_code == (204 if allowed else 404)
    contact.refresh_from_db()
    assert contact.is_active is not allowed


@pytest.mark.parametrize("actor", list(ACTORS))
def test_helplines_are_readable_by_every_official_and_editable_by_the_super_admin_only(world, api, number, actor):
    contact = number("ap", "health", "Ambulance", "108")
    client, url = api(world.users[actor]), f"{ADMIN_URL}{contact.id}/"
    may_edit = ACTORS[actor][1]
    detail = client.get(url)
    assert detail.status_code == 200
    assert detail.json()["allowed_actions"] == (["edit", "remove", "verify"] if may_edit else [])
    assert client.patch(url, {"name": "Ambulance 108"}, format="json").status_code == (200 if may_edit else 404)
    assert client.delete(url).status_code == (204 if may_edit else 404)


@pytest.mark.parametrize("actor", list(ACTORS))
def test_the_list_shows_exactly_the_numbers_in_reach(world, api, number, actor):
    for key in AREAS:
        number(key, name=key)
    rows = api(world.users[actor]).get(ADMIN_URL, {"page_size": 100}).json()["results"]
    assert {row["name"] for row in rows} == {key for key in AREAS if reaches(actor, key)}
    assert all(row["allowed_actions"] == ["edit", "remove", "verify"] for row in rows)


def test_moving_needs_reach_over_the_new_area_too(world, api, number):
    contact = number("kolakaluru")
    client, url = api(world.users["ma_tenali"]), f"{ADMIN_URL}{contact.id}/"
    refused = client.patch(url, {"area": world.area_json("kaza")}, format="json")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "out_of_area"
    contact.refresh_from_db()
    assert contact.area == world.ref("kolakaluru")
    moved = client.patch(url, {"area": world.area_json("nandivelugu")}, format="json")
    assert moved.status_code == 200 and moved.json()["area"]["id"] == world.ref("nandivelugu").id


def test_panchayat_members_manage_only_their_own_village(world, api, number):
    client = api(world.users["pm_kolakaluru"])
    assert client.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").status_code == 201
    assert client.post(ADMIN_URL, area_body(world, "tenali", phone="08644222334"), format="json").status_code == 403


def test_anonymous_gets_401(world, api, number):
    contact = number("kolakaluru")
    client = api()
    assert client.get(ADMIN_URL).status_code == 401
    assert client.post(ADMIN_URL, area_body(world, "kolakaluru"), format="json").status_code == 401
    assert client.get(f"{ADMIN_URL}{contact.id}/").status_code == 401
    assert client.get(f"{ADMIN_URL}targets/").status_code == 401


def test_two_assignments_do_not_leak(world, api, number):
    """A Village Admin of Kolakaluru who is also a Panchayat member of Kaza manages both villages, nothing more."""
    from conftest import assign

    from apps.accounts.roles import Role

    assign(world, "va_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")
    client = api(world.users["va_kolakaluru"])
    assert client.post(ADMIN_URL, area_body(world, "kaza"), format="json").status_code == 201
    assert client.post(ADMIN_URL, area_body(world, "chinakakani"), format="json").status_code == 403

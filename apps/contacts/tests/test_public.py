"""What a citizen sees: GET /contacts/?village_id= and GET /contact-types/ (contacts plan, sections 3 and 7)."""

from datetime import datetime

import pytest

from apps.contacts.models import ContactType
from apps.geography.models import Mandal, Village
from conftest import PARENT, inside

from .conftest import URL

pytestmark = pytest.mark.django_db

ALL_KEYS = ["ap", *PARENT]


def get(api, world, village="kolakaluru", **headers):
    return api().get(URL, {"village_id": world.ref(village).id}, **headers)


def shown(data) -> dict[str, list[str]]:
    return {section: [c["name"] for g in data[section] for c in g["contacts"]] for section in ("for_you", "helplines")}


def test_a_village_sees_itself_its_mandal_its_district_and_its_state(world, api, number):
    for key in ALL_KEYS:
        if key == "ap":
            number("ap", "health", "State 108", "108")
        else:
            number(key, name=f"Office {key}")
    data = get(api, world, HTTP_ACCEPT_LANGUAGE="en").json()
    expected_area = [f"Office {k}" for k in ("kolakaluru", "tenali", "guntur")]  # most local first
    assert shown(data) == {"for_you": expected_area, "helplines": ["State 108"]}
    assert {k for k in ALL_KEYS if k != "ap" and inside("kolakaluru", k)} == {"kolakaluru", "tenali", "guntur"}
    assert data["village"]["path"] == "Kolakaluru · Tenali · Guntur"


def test_hidden_numbers_types_and_other_places_never_show(world, api, number):
    number("kolakaluru", name="Shown")
    number("kolakaluru", name="Removed", is_active=False)
    number("kolakaluru", "meeseva", name="Type switched off")
    ContactType.objects.filter(key="meeseva").update(is_active=False)
    number("nandivelugu", name="Sibling village")
    number("mangalagiri", name="Sibling mandal")
    number("nidamanuru", name="Other district")
    assert shown(get(api, world).json())["for_you"] == ["Shown"]


def test_inactive_village_or_mandal_is_404_and_village_id_is_required(world, api):
    assert api().get(URL).status_code == 400
    assert api().get(URL, {"village_id": "x"}).status_code == 400
    assert api().get(URL, {"village_id": 999999}).status_code == 404
    Mandal.objects.filter(pk=world.ref("tenali").id).update(is_active=False)
    assert get(api, world).status_code == 404
    Mandal.objects.filter(pk=world.ref("tenali").id).update(is_active=True)
    Village.objects.filter(pk=world.ref("kolakaluru").id).update(is_active=False)
    assert get(api, world).status_code == 404


def test_groups_follow_the_types_order_and_numbers_the_most_local_first(world, api, number):
    number("guntur", "official", "Collectorate", sort_order=1)
    number("kolakaluru", "official", "Panchayat office", sort_order=50)
    number("tenali", "official", "MPDO B", sort_order=10)
    number("tenali", "official", "MPDO A", sort_order=10)
    number("kolakaluru", "vro", "K. Srinivasa Rao", "9000010001")
    data = get(api, world, HTTP_ACCEPT_LANGUAGE="en").json()
    assert [(g["type"], g["label"]) for g in data["for_you"]] == [("vro", "VRO"), ("official", "Govt. official")]
    officials = [c["name"] for c in data["for_you"][1]["contacts"]]
    assert officials == ["Panchayat office", "MPDO A", "MPDO B", "Collectorate"]  # village, mandal (order, name), district


def test_other_numbers_group_by_the_typed_name(world, api, number):
    number("kolakaluru", "other", "Anganwadi Kolakaluru", custom_type="Anganwadi")
    number("tenali", "other", "Anganwadi Tenali", custom_type="anganwadi")
    number("tenali", "other", "Bus depot", custom_type="Bus depot")
    groups = get(api, world, HTTP_ACCEPT_LANGUAGE="en").json()["for_you"]
    assert [(g["type"], g["label"], len(g["contacts"])) for g in groups] == [
        ("other", "Anganwadi", 2), ("other", "Bus depot", 1),
    ]


def test_contact_shape_and_language(world, api, number):
    number("kolakaluru", "vro", "K. Srinivasa Rao", "9000010001", name_te="కె. శ్రీనివాసరావు", email="vro@example.in")
    number("tenali", "official", "MPDO Office")  # no Telugu name: falls back to the English one
    telugu = get(api, world).json()
    vro = telugu["for_you"][0]
    assert vro["label"] == "వీఆర్వో"
    assert vro["contacts"][0] == {
        "id": vro["contacts"][0]["id"],
        "name": "కె. శ్రీనివాసరావు",
        "phone": "9000010001",
        "phone_kind": "MOBILE",
        "email": "vro@example.in",
        "area": {"level": "VILLAGE", "id": world.ref("kolakaluru").id, "name": "కొలకలూరు"},
    }
    assert telugu["for_you"][1]["contacts"][0]["name"] == "MPDO Office"
    assert telugu["for_you"][1]["contacts"][0]["email"] is None
    english = get(api, world, HTTP_ACCEPT_LANGUAGE="en").json()
    assert english["for_you"][0]["label"] == "VRO" and english["for_you"][0]["contacts"][0]["name"] == "K. Srinivasa Rao"


def test_empty_sections_and_updated_at(world, api, number):
    empty = get(api, world).json()
    assert empty["for_you"] == [] and empty["helplines"] == [] and empty["updated_at"] is None
    contact = number("kolakaluru")
    sent = get(api, world).json()["updated_at"]
    assert sent.endswith("+05:30")  # India Standard Time, like every time in the API
    assert datetime.fromisoformat(sent) == contact.updated_at


def test_cacheable_with_an_etag_and_304_until_something_changes(world, api, number):
    contact = number("kolakaluru")
    first = get(api, world)
    assert "max-age=300" in first["Cache-Control"] and "public" in first["Cache-Control"]
    assert get(api, world, HTTP_IF_NONE_MATCH=first["ETag"]).status_code == 304
    contact.name = "Renamed"
    contact.save()
    assert get(api, world, HTTP_IF_NONE_MATCH=first["ETag"]).status_code == 200
    english = get(api, world, HTTP_ACCEPT_LANGUAGE="en")
    assert english["ETag"] != get(api, world)["ETag"]  # the language changes the body, so the ETag


def test_a_stale_token_is_ignored(world, api, number):
    number("kolakaluru")
    client = api()
    client.credentials(HTTP_AUTHORIZATION="Bearer stale-token-from-an-old-login")
    assert client.get(URL, {"village_id": world.ref("kolakaluru").id}).status_code == 200


def test_contact_types(world, api):
    ContactType.objects.filter(key="railways").update(is_active=False)
    response = api().get("/api/v1/contact-types/", HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200 and "max-age=300" in response["Cache-Control"]  # changes show within 5 minutes
    assert "public" in response["Cache-Control"]
    rows = response.json()
    assert [r["key"] for r in rows if r["section"] == "AREA"] == ["vro", "meeseva", "sachivalayam", "revenue", "official", "other"]
    assert "railways" not in {r["key"] for r in rows}
    other = next(r for r in rows if r["key"] == "other")
    assert other == {"key": "other", "section": "AREA", "name": "Other", "is_other": True, "sort_order": other["sort_order"]}
    assert api().get("/api/v1/contact-types/").json()[0]["name"] == "వీఆర్వో"

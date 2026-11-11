"""The officials' service-request list prepares a whole page at once (audit finding BE-DB-006).

assigned_to and allowed_actions are checked row by row against the viewer's and the assignee's rights; the
expected values here are worked out from the rules (contract section 16):
- Only the assignee changes a request: set_status and assign.
- An admin of the area may assign a request nobody holds: never assigned, or the assignee was blocked or
  has no role over the village any more. Otherwise there is nothing to do.
- assigned_to.role is the assignee's role over the village, or "" when they have none (blocked included).
"""

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import authz
from apps.accounts.models import User
from apps.citizens.models import CitizenDevice
from apps.service_requests.models import Service, ServiceRequest

pytestmark = pytest.mark.django_db
LIST = "/api/v1/service-requests/?page_size=100"


def make_request(world, village, holder=None):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref(village).id, nonce=uuid.uuid4().hex,
    )
    return ServiceRequest.objects.create(
        service=Service.objects.first(), name="రాము", phone="9000000000", address="a",
        village_id=world.ref(village).id, device=device, assigned_to=world.users[holder] if holder else None,
    )


def rows(api, world, viewer):
    response = api(world.users[viewer]).get(LIST)
    assert response.status_code == 200
    return {row["id"]: row for row in response.json()["results"]}


def test_assignee_and_buttons_for_a_district_admin(api, world):
    held = [make_request(world, "kolakaluru", "va_kolakaluru") for _ in range(3)]  # one person, three rows
    unheld = make_request(world, "kaza")
    blocked = make_request(world, "kaza", "va_kaza")
    mine = make_request(world, "nandivelugu", "da_guntur")
    wrong_village = make_request(world, "kolakaluru", "va_nandivelugu")  # no role over Kolakaluru
    elsewhere = make_request(world, "nidamanuru", "va_nidamanuru")  # Krishna district
    User.objects.filter(pk=world.users["va_kaza"].pk).update(is_active=False)

    seen = rows(api, world, "da_guntur")
    assert elsewhere.id not in seen
    for request in held:
        assert seen[request.id]["assigned_to"]["role"] == "VILLAGE_ADMIN"
        assert seen[request.id]["allowed_actions"] == []  # its holder can act
    assert seen[unheld.id]["assigned_to"] is None and seen[unheld.id]["allowed_actions"] == ["assign"]
    assert seen[blocked.id]["assigned_to"]["role"] == "" and seen[blocked.id]["allowed_actions"] == ["assign"]
    assert seen[mine.id]["assigned_to"]["role"] == "DISTRICT_ADMIN"
    assert seen[mine.id]["allowed_actions"] == ["set_status", "assign"]
    assert seen[wrong_village.id]["assigned_to"]["role"] == ""
    assert seen[wrong_village.id]["allowed_actions"] == ["assign"]


def test_buttons_for_the_assignee_and_a_village_admin(api, world):
    own = make_request(world, "kolakaluru", "va_kolakaluru")
    stuck = make_request(world, "kolakaluru", "va_nandivelugu")
    seen = rows(api, world, "va_kolakaluru")
    assert seen[own.id]["allowed_actions"] == ["set_status", "assign"]
    assert seen[stuck.id]["allowed_actions"] == ["assign"]


def queries(client):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(LIST).status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.parametrize("viewer", ["da_guntur", "super", "va_kolakaluru"])
def test_queries_do_not_grow_with_rows(api, world, viewer):
    client = api(world.users[viewer])
    make_request(world, "kolakaluru", "va_kolakaluru")
    one = queries(client)
    holders = ["va_kolakaluru", "va_nandivelugu", "va_kaza", "da_guntur", "ma_tenali", None]
    villages = ["kolakaluru", "nandivelugu", "kaza", "chinakakani"]
    for index in range(16):
        make_request(world, villages[index % 4], holders[index % 6])
    assert queries(client) <= one


def test_prime_users_gives_each_official_the_grants_of_a_lone_load(world):
    lone = {name: authz.grants(User.objects.get(pk=user.pk)) for name, user in world.users.items()}
    fresh = {name: User.objects.get(pk=user.pk) for name, user in world.users.items()}
    duplicate = User.objects.get(pk=world.users["va_kaza"].pk)  # a second object of the same person
    with CaptureQueriesContext(connection) as ctx:
        authz.prime_users([*fresh.values(), duplicate])
    assert len(ctx.captured_queries) == 2
    assert {name: authz.grants(user) for name, user in fresh.items()} == lone
    assert authz.grants(duplicate) == lone["va_kaza"]

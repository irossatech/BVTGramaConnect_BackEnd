"""The officials list need not carry full mobiles and emails (BVTGC-75, audit CROSS-PII-002).

GET /users/?contact=0 leaves `mobile` and `email` out of every row and sends `mobile_last4` and
`email_masked` instead; GET /users/{id}/ (one official, inside the actor's reach) has them in full.
Without contact=0 the rows are as before, so current clients keep working until they switch.
"""

import pytest

from apps.accounts.models import User

pytestmark = pytest.mark.django_db
USERS = "/api/v1/users/"


def rows(client, **params):
    response = client.get(USERS, params)
    assert response.status_code == 200
    return {row["username"]: row for row in response.json()["results"]}


def test_contact_0_rows_have_no_full_mobile_or_email(api, world):
    listed = rows(api(world.users["super"]), contact="0", page_size=100)
    assert listed
    for row in listed.values():
        assert "mobile" not in row and "email" not in row
    official = world.users["va_kolakaluru"]
    row = listed[official.username]
    assert row["mobile_last4"] == official.mobile[-4:]
    assert row["email_masked"] == f"{official.email[0]}***@example.org"
    assert official.mobile not in str(listed) and official.email not in str(listed)


def test_without_contact_0_the_rows_are_unchanged(api, world):
    official = world.users["va_kolakaluru"]
    row = rows(api(world.users["super"]), page_size=100)[official.username]
    assert row["mobile"] == official.mobile and row["email"] == official.email
    assert row["mobile_last4"] == official.mobile[-4:]  # the new fields are there too


def test_one_official_always_has_the_full_details(api, world):
    official = world.users["va_kolakaluru"]
    data = api(world.users["super"]).get(f"{USERS}{official.pk}/", {"contact": "0"}).json()
    assert data["mobile"] == official.mobile and data["email"] == official.email


def test_search_by_mobile_still_works_with_contact_0(api, world):
    official = world.users["va_kolakaluru"]
    listed = rows(api(world.users["super"]), contact="0", q=official.mobile[-6:])
    assert official.username in listed


def test_an_official_without_mobile_or_email_gets_empty_masks(api, world):
    official = world.users["va_kolakaluru"]
    User.objects.filter(pk=official.pk).update(mobile=None, email=None)
    row = rows(api(world.users["super"]), contact="0", page_size=100)[official.username]
    assert row["mobile_last4"] == "" and row["email_masked"] == ""


def test_lists_stay_inside_the_actors_area(api, world):
    """contact=0 changes the shape only, never who is listed."""
    actor = world.users["va_kolakaluru"]
    with_contact = rows(api(actor), page_size=100)
    without = rows(api(actor), contact="0", page_size=100)
    assert set(with_contact) == set(without)

"""GET /users/?role=SUPER_ADMIN, the console's "Super Admin" choice in the Users page's role filter (2026-11-26).

The Super Admin sees every other official, the other Super Admins included (the list never holds the viewer
themself, authz.manageable_users), so the filter lists exactly the other Super Admins.
A District Admin manages no Super Admin, so the same filter gives them an empty list: a query parameter
only narrows a list, never widens it (conventions, Permissions).
"""

import pytest

from apps.accounts.roles import Role
from conftest import OFFICIALS, make_official

pytestmark = pytest.mark.django_db

USERS = "/api/v1/users/"


def usernames(response) -> set[str]:
    assert response.status_code == 200, response.json()
    body = response.json()
    return {row["username"] for row in (body["results"] if isinstance(body, dict) else body)}


def test_the_super_admin_filter_lists_the_other_super_admins(api, world):
    make_official(world, "super_two", Role.SUPER_ADMIN, "ap", "9100000001")
    make_official(world, "super_three", Role.SUPER_ADMIN, "ap", "9100000002")
    response = api(world.users["super"]).get(USERS, {"role": Role.SUPER_ADMIN, "page_size": 100})
    assert usernames(response) == {"super_two", "super_three"}


@pytest.mark.parametrize("role", [Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER])
def test_the_other_choices_list_only_their_role(api, world, role):
    response = api(world.users["super"]).get(USERS, {"role": role, "page_size": 100})
    assert usernames(response) == {name for name, (r, _) in OFFICIALS.items() if r == role}


def test_a_district_admin_sees_no_super_admin_through_the_filter(api, world):
    response = api(world.users["da_guntur"]).get(USERS, {"role": Role.SUPER_ADMIN, "page_size": 100})
    assert usernames(response) == set()

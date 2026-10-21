"""user_areas(), is_within() and the ORM filter that mirrors is_within()."""

import itertools

import pytest

from apps.accounts.models import RoleAssignment
from apps.accounts.roles import Role
from apps.accounts.scope import (
    RoleArea,
    can_manage,
    capabilities,
    forget_areas,
    is_within,
    manageable_roles,
    user_areas,
)
from conftest import PARENT, inside
from apps.geography.areas import AreaRef, within_q

pytestmark = pytest.mark.django_db

ALL_AREAS = ["ap", *PARENT.keys()]


@pytest.mark.parametrize("area_key, container_key", list(itertools.product(ALL_AREAS, ALL_AREAS)))
def test_is_within_matches_the_map(geo, area_key, container_key):
    assert is_within(geo.ref(area_key), geo.ref(container_key)) == inside(area_key, container_key)


def test_within_q_selects_exactly_what_is_within_says(world):
    """The SQL filter used for lists must agree with the Python check for every pair."""
    rows = []
    for key in ALL_AREAS:
        ref = world.ref(key)
        rows.append(RoleAssignment(user=world.users["super"], role=Role.PANCHAYAT_MEMBER, area_level=ref.level, area_id=ref.id))
    RoleAssignment.objects.bulk_create(rows)
    probe = RoleAssignment.objects.filter(user=world.users["super"], role=Role.PANCHAYAT_MEMBER)
    for container_key in ALL_AREAS:
        container = world.ref(container_key)
        found = {AreaRef(level, area_id) for level, area_id in probe.filter(within_q(container)).values_list("area_level", "area_id")}
        expected = {world.ref(key) for key in ALL_AREAS if inside(key, container_key)}
        assert found == expected, container_key


def test_missing_area_is_never_within(geo):
    assert not is_within(AreaRef("VILLAGE", 999999), geo.ref("ap"))


def test_user_areas_lists_every_assignment(world):
    user = world.users["ma_tenali"]
    RoleAssignment.objects.create(user=user, role=Role.VILLAGE_ADMIN, area_level="VILLAGE", area_id=world.ref("kaza").id)
    forget_areas(user)
    assert set(user_areas(user)) == {
        RoleArea(Role.MANDAL_ADMIN, world.ref("tenali")),
        RoleArea(Role.VILLAGE_ADMIN, world.ref("kaza")),
    }
    assert user_areas(user, roles=[Role.VILLAGE_ADMIN]) == [RoleArea(Role.VILLAGE_ADMIN, world.ref("kaza"))]


def test_super_admin_reaches_everywhere(world):
    reach = user_areas(world.users["super"])[0]
    assert is_within(world.ref("kanuru"), reach)
    assert is_within(AreaRef("STATE", 12345), reach)


def test_several_assignments_add_up(world):
    """A Mandal Admin of Tenali who is also Village Admin of Kaza."""
    user = world.users["ma_tenali"]
    RoleAssignment.objects.create(user=user, role=Role.VILLAGE_ADMIN, area_level="VILLAGE", area_id=world.ref("kaza").id)
    forget_areas(user)
    assert manageable_roles(user) == [Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER]
    assert can_manage(user, Role.PANCHAYAT_MEMBER, world.ref("kaza"))
    assert can_manage(user, Role.VILLAGE_ADMIN, world.ref("nandivelugu"))
    assert not can_manage(user, Role.VILLAGE_ADMIN, world.ref("kaza"))


@pytest.mark.parametrize(
    "name, expected",
    [
        ("super", {"can_post": False, "can_review": True, "can_manage_users": True, "is_super_admin": True}),
        ("da_guntur", {"can_post": True, "can_review": True, "can_manage_users": True, "is_super_admin": False}),
        ("va_kolakaluru", {"can_post": True, "can_review": True, "can_manage_users": True, "is_super_admin": False}),
        ("pm_kolakaluru", {"can_post": True, "can_review": False, "can_manage_users": False, "is_super_admin": False}),
    ],
)
def test_capabilities(world, name, expected):
    caps = capabilities(world.users[name])
    assert {key: caps[key] for key in expected} == expected


def test_super_admin_can_post_only_when_the_setting_allows(world, settings):
    settings.SUPER_ADMIN_CAN_POST = True
    assert capabilities(world.users["super"])["can_post"] is True

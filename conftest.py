"""Shared test fixtures: a small map of Andhra Pradesh and one official per role and area.

Map (English key, Telugu name):

    ap  Andhra Pradesh
    ├── guntur (district)
    │   ├── tenali (mandal):       kolakaluru, nandivelugu
    │   └── mangalagiri (mandal):  kaza, chinakakani
    └── krishna (district)
        ├── gannavaram (mandal):   nidamanuru, kesarapalli
        └── penamaluru (mandal):   poranki, kanuru

Officials are named after role and area: "da_guntur", "ma_tenali",
"va_kolakaluru", "pm_kolakaluru" and so on. See OFFICIALS. Each one's username
is that name, the email is <name>@example.org and the password TEST_PASSWORD.
Roles are the Django groups that migrations create from role_matrix.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.accounts.roles import Role
from apps.accounts.tokens import issue_tokens
from apps.geography.areas import AreaRef
from apps.geography.models import District, Mandal, State, Village

MAP = {
    "guntur": ("గుంటూరు", {
        "tenali": ("తెనాలి", {
            "kolakaluru": ("కొలకలూరు", "16.238100", "80.592400"),
            "nandivelugu": ("నందివెలుగు", "16.217600", "80.623200"),
        }),
        "mangalagiri": ("మంగళగిరి", {
            "kaza": ("కాజా", "16.410600", "80.549000"),
            "chinakakani": ("చినకాకాని", "16.451200", "80.558100"),
        }),
    }),
    "krishna": ("కృష్ణా", {
        "gannavaram": ("గన్నవరం", {
            "nidamanuru": ("నిడమానూరు", "16.531200", "80.712400"),
            "kesarapalli": ("కేసరపల్లి", "16.530000", "80.800000"),
        }),
        "penamaluru": ("పెనమలూరు", {
            "poranki": ("పోరంకి", "16.474000", "80.705000"),
            "kanuru": ("కానూరు", "16.488000", "80.690000"),
        }),
    }),
}

#: Parent of every area key, for tests that work out containment on their own.
PARENT: dict[str, str] = {}
for _district, (_te, _mandals) in MAP.items():
    PARENT[_district] = "ap"
    for _mandal, (_te, _villages) in _mandals.items():
        PARENT[_mandal] = _district
        for _village in _villages:
            PARENT[_village] = _mandal


def ancestors(key: str) -> list[str]:
    chain = [key]
    while chain[-1] in PARENT:
        chain.append(PARENT[chain[-1]])
    return chain


def inside(area_key: str, container_key: str) -> bool:
    return container_key in ancestors(area_key)


#: name -> (role, area key). Mobile numbers are generated in order.
OFFICIALS = {
    "super": (Role.SUPER_ADMIN, "ap"),
    "da_guntur": (Role.DISTRICT_ADMIN, "guntur"),
    "da_krishna": (Role.DISTRICT_ADMIN, "krishna"),
    "ma_tenali": (Role.MANDAL_ADMIN, "tenali"),
    "ma_mangalagiri": (Role.MANDAL_ADMIN, "mangalagiri"),
    "ma_gannavaram": (Role.MANDAL_ADMIN, "gannavaram"),
    "va_kolakaluru": (Role.VILLAGE_ADMIN, "kolakaluru"),
    "va_nandivelugu": (Role.VILLAGE_ADMIN, "nandivelugu"),
    "va_kaza": (Role.VILLAGE_ADMIN, "kaza"),
    "va_nidamanuru": (Role.VILLAGE_ADMIN, "nidamanuru"),
    "pm_kolakaluru": (Role.PANCHAYAT_MEMBER, "kolakaluru"),
    "pm_nandivelugu": (Role.PANCHAYAT_MEMBER, "nandivelugu"),
    "pm_kaza": (Role.PANCHAYAT_MEMBER, "kaza"),
    "pm_nidamanuru": (Role.PANCHAYAT_MEMBER, "nidamanuru"),
}


@dataclass
class World:
    state: State
    areas: dict[str, object] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)

    def ref(self, key: str) -> AreaRef:
        obj = self.areas[key]
        return AreaRef(obj.level, obj.id)

    def area_json(self, key: str) -> dict:
        ref = self.ref(key)
        return {"level": ref.level, "id": ref.id}


def build_map() -> World:
    state = State.objects.create(name_en="Andhra Pradesh", name_te="ఆంధ్రప్రదేశ్")
    world = World(state=state, areas={"ap": state})
    for d_key, (d_te, mandals) in MAP.items():
        district = District.objects.create(state=state, name_en=d_key.title(), name_te=d_te)
        world.areas[d_key] = district
        for m_key, (m_te, villages) in mandals.items():
            mandal = Mandal.objects.create(district=district, name_en=m_key.title(), name_te=m_te)
            world.areas[m_key] = mandal
            for v_key, (v_te, lat, lng) in villages.items():
                world.areas[v_key] = Village.objects.create(
                    mandal=mandal, name_en=v_key.title(), name_te=v_te,
                    latitude=Decimal(lat), longitude=Decimal(lng), pin_code="522306",
                )
    return world


#: Password every test official gets, for tests that log in through /auth/login/.
TEST_PASSWORD = "Grama-Test-2026"


def make_official(world: World, name: str, role: str, area_key: str, mobile: str) -> User:
    """Username is the official's name ("va_kolakaluru"), email <name>@example.org."""
    user = User.objects.create_user(
        username=name, email=f"{name}@example.org", mobile=mobile, name=name, password=TEST_PASSWORD,
    )
    RoleAssignment.objects.assign(user, role, world.ref(area_key))
    return user


def assign(world: World, name: str, role: str, area_key: str, **kwargs) -> RoleAssignment:
    """Give an official another role: assign(world, "pm_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")."""
    return RoleAssignment.objects.assign(world.users[name], role, world.ref(area_key), **kwargs)


@pytest.fixture
def geo(db) -> World:
    return build_map()


@pytest.fixture
def world(geo) -> World:
    for index, (name, (role, area_key)) in enumerate(OFFICIALS.items()):
        geo.users[name] = make_official(geo, name, role, area_key, f"90000{index:05d}")
    return geo


def client_for(user: User | None = None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user)['access']}")
    return client


@pytest.fixture
def api():
    """api(user) gives a client logged in as that user; api() an anonymous one."""
    return client_for


@pytest.fixture
def super_admin_can_post(db, settings):
    """Switch SUPER_ADMIN_CAN_POST on and re-sync the groups, as a deploy would.

    super_admin_can_post(False) switches it off again in the same test. The
    test's transaction rollback puts the permissions back afterwards.
    """
    from apps.accounts.sync import sync_role_permissions

    def switch(allowed: bool = True):
        settings.SUPER_ADMIN_CAN_POST = allowed
        sync_role_permissions()

    switch(True)
    return switch


def console_client_for(user: User) -> APIClient:
    """A client with a web console session for `user`, as POST /auth/session/login/ would leave it."""
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def console():
    """console(user) gives a client logged in to the web console (session cookie)."""
    return console_client_for

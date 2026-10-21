"""The user-management permission matrix (CLAUDE.md "Roles", contract section 11).

Expected outcomes are worked out here from the rules as written in CLAUDE.md
and from the map's parent links in conftest.py. They do not call the code
under test, so a bug in accounts.scope cannot hide itself.

Every official on the map is tried against every combination:
- creating each role in each area (allowed, wrong role, outside the area),
- which users appear in their list,
- reading and blocking every other official.
"""

import itertools

import pytest

from apps.accounts.models import RoleAssignment, User, UserStatus
from apps.accounts.roles import Role
from conftest import OFFICIALS, inside

pytestmark = pytest.mark.django_db

# CLAUDE.md, "Roles": who creates and blocks whom.
CAN_MANAGE = {
    Role.SUPER_ADMIN: {Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.DISTRICT_ADMIN: {Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.MANDAL_ADMIN: {Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.VILLAGE_ADMIN: {Role.PANCHAYAT_MEMBER},
    Role.PANCHAYAT_MEMBER: set(),
}

AREAS_BY_ROLE = {
    Role.SUPER_ADMIN: ["ap"],
    Role.DISTRICT_ADMIN: ["guntur", "krishna"],
    Role.MANDAL_ADMIN: ["tenali", "mangalagiri", "gannavaram"],
    Role.VILLAGE_ADMIN: ["kolakaluru", "nandivelugu", "kaza", "nidamanuru"],
    Role.PANCHAYAT_MEMBER: ["kolakaluru", "nandivelugu", "kaza", "nidamanuru"],
}

ACTORS = list(OFFICIALS)
CREATE_CASES = [
    (actor, role, area)
    for actor in ACTORS
    for role, areas in AREAS_BY_ROLE.items()
    for area in areas
]


def expected_outcome(actor: str, role: str, area: str) -> tuple[int, str | None]:
    actor_role, actor_area = OFFICIALS[actor]
    if not CAN_MANAGE[actor_role]:
        return 403, "permission_denied"
    if role not in CAN_MANAGE[actor_role]:
        return 403, "permission_denied"
    if actor_role != Role.SUPER_ADMIN and not inside(area, actor_area):
        return 403, "out_of_area"
    return 201, None


def may_manage(actor: str, target: str) -> bool:
    if actor == target:
        return False
    actor_role, actor_area = OFFICIALS[actor]
    role, area = OFFICIALS[target]
    if role not in CAN_MANAGE[actor_role]:
        return False
    return actor_role == Role.SUPER_ADMIN or inside(area, actor_area)


def assert_outcome(response, status: int, code: str | None):
    assert response.status_code == status, response.content
    if code:
        assert response.json()["error"]["code"] == code


# --- Creating users ------------------------------------------------------------------


@pytest.mark.parametrize("actor, role, area", CREATE_CASES)
def test_create_user_matrix(api, world, actor, role, area):
    response = api(world.users[actor]).post(
        "/api/v1/users/",
        {"name": "New Official", "email": "new.official@example.org", "role": role, "area": world.area_json(area)},
        format="json",
    )
    status, code = expected_outcome(actor, role, area)
    assert_outcome(response, status, code)
    created = User.objects.filter(email="new.official@example.org")
    assert created.exists() == (status == 201)
    if status == 201:
        assignment = created.get().role_assignments.get()
        assert (assignment.role, assignment.area_level, assignment.area_id) == (
            role, world.ref(area).level, world.ref(area).id,
        )
        assert created.get().created_by == world.users[actor]


# --- Who sees whom -------------------------------------------------------------------------


@pytest.mark.parametrize("actor", ACTORS)
def test_list_shows_exactly_the_users_an_admin_may_manage(api, world, actor):
    response = api(world.users[actor]).get("/api/v1/users/?page_size=100")
    if not CAN_MANAGE[OFFICIALS[actor][0]]:
        assert_outcome(response, 403, "permission_denied")
        return
    assert response.status_code == 200
    seen = {row["name"] for row in response.json()["results"]}
    expected = {target for target in OFFICIALS if may_manage(actor, target)}
    assert seen == expected
    assert response.json()["count"] == len(expected)


@pytest.mark.parametrize("actor, target", list(itertools.product(ACTORS, ACTORS)))
def test_read_user_matrix(api, world, actor, target):
    response = api(world.users[actor]).get(f"/api/v1/users/{world.users[target].id}/")
    if not CAN_MANAGE[OFFICIALS[actor][0]]:
        assert_outcome(response, 403, "permission_denied")
    elif may_manage(actor, target):
        assert response.status_code == 200
        assert response.json()["name"] == target
    else:
        assert_outcome(response, 404, "not_found")


@pytest.mark.parametrize("actor, target", list(itertools.product(ACTORS, ACTORS)))
def test_block_user_matrix(api, world, actor, target):
    victim = world.users[target]
    response = api(world.users[actor]).post(f"/api/v1/users/{victim.id}/block/", {}, format="json")
    victim.refresh_from_db()
    if not CAN_MANAGE[OFFICIALS[actor][0]]:
        assert_outcome(response, 403, "permission_denied")
        assert victim.status == UserStatus.ACTIVE
    elif may_manage(actor, target):
        assert response.status_code == 200
        assert victim.status == UserStatus.BLOCKED
        assert victim.token_version == 1
    else:
        assert_outcome(response, 404, "not_found")
        assert victim.status == UserStatus.ACTIVE
        assert victim.token_version == 0


@pytest.mark.parametrize(
    "actor, target, allowed",
    [
        ("va_kolakaluru", "pm_kolakaluru", True),
        ("va_kolakaluru", "pm_nandivelugu", False),
        ("ma_tenali", "va_kolakaluru", True),
        ("ma_tenali", "va_kaza", False),
        ("da_guntur", "ma_tenali", True),
        ("da_guntur", "ma_gannavaram", False),
        ("da_guntur", "da_krishna", False),
        ("super", "da_krishna", True),
    ],
)
def test_unblock_and_rename_follow_the_same_rule(api, world, actor, target, allowed):
    victim = world.users[target]
    User.objects.filter(pk=victim.pk).update(status=UserStatus.BLOCKED)
    client = api(world.users[actor])
    unblock = client.post(f"/api/v1/users/{victim.id}/unblock/", {}, format="json")
    rename = client.patch(f"/api/v1/users/{victim.id}/", {"name_te": "కొత్త పేరు"}, format="json")
    victim.refresh_from_db()
    if allowed:
        assert unblock.status_code == 200 and rename.status_code == 200
        assert victim.status == UserStatus.ACTIVE and victim.name_te == "కొత్త పేరు"
    else:
        assert unblock.status_code == 404 and rename.status_code == 404
        assert victim.status == UserStatus.BLOCKED and victim.name_te != "కొత్త పేరు"


# --- Users with several assignments ---------------------------------------------------------


def add_assignment(world, name, role, area):
    ref = world.ref(area)
    RoleAssignment.objects.create(user=world.users[name], role=role, area_level=ref.level, area_id=ref.id)


@pytest.mark.parametrize(
    "actor, visible",
    [
        ("va_kolakaluru", False),  # the Kaza membership is outside the village
        ("ma_tenali", False),      # Kaza is in Mangalagiri mandal
        ("da_guntur", True),       # both villages are in Guntur
        ("da_krishna", False),
        ("super", True),
    ],
)
def test_every_assignment_must_be_inside_the_admins_reach(api, world, actor, visible):
    add_assignment(world, "pm_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")
    target = world.users["pm_kolakaluru"]
    response = api(world.users[actor]).get(f"/api/v1/users/{target.id}/")
    assert response.status_code == (200 if visible else 404)


def test_a_user_who_is_also_an_admin_is_out_of_reach_of_peers(api, world):
    """pm_nandivelugu also becomes Mandal Admin of Tenali: Village Admins lose them."""
    add_assignment(world, "pm_nandivelugu", Role.MANDAL_ADMIN, "tenali")
    target = world.users["pm_nandivelugu"]
    assert api(world.users["va_nandivelugu"]).get(f"/api/v1/users/{target.id}/").status_code == 404
    assert api(world.users["ma_tenali"]).get(f"/api/v1/users/{target.id}/").status_code == 404
    assert api(world.users["da_guntur"]).get(f"/api/v1/users/{target.id}/").status_code == 200


def test_actor_with_two_assignments_combines_them(api, world):
    """Mandal Admin of Tenali who is also Village Admin of Kaza."""
    add_assignment(world, "ma_tenali", Role.VILLAGE_ADMIN, "kaza")
    client = api(world.users["ma_tenali"])

    def create(email, role, area):
        return client.post(
            "/api/v1/users/",
            {"name": "X", "email": email, "role": role, "area": world.area_json(area)},
            format="json",
        )

    assert create("x1@example.org", Role.PANCHAYAT_MEMBER, "kaza").status_code == 201
    assert create("x2@example.org", Role.VILLAGE_ADMIN, "nandivelugu").status_code == 201
    assert_outcome(create("x3@example.org", Role.VILLAGE_ADMIN, "kaza"), 403, "out_of_area")
    assert_outcome(create("x4@example.org", Role.PANCHAYAT_MEMBER, "chinakakani"), 403, "out_of_area")


# --- Role assignments -------------------------------------------------------------------------


def assignments_url(world, name):
    return f"/api/v1/users/{world.users[name].id}/role-assignments/"


def assignment_url(world, name):
    assignment = world.users[name].role_assignments.get()
    return f"{assignments_url(world, name)}{assignment.id}/"


@pytest.mark.parametrize(
    "actor, role, area, status, code",
    [
        ("ma_tenali", Role.PANCHAYAT_MEMBER, "nandivelugu", 201, None),
        ("ma_tenali", Role.PANCHAYAT_MEMBER, "kaza", 403, "out_of_area"),
        ("ma_tenali", Role.MANDAL_ADMIN, "tenali", 403, "permission_denied"),
        ("va_kolakaluru", Role.VILLAGE_ADMIN, "kolakaluru", 403, "permission_denied"),
        ("va_kolakaluru", Role.PANCHAYAT_MEMBER, "kolakaluru", 409, "duplicate"),
        ("va_nandivelugu", Role.PANCHAYAT_MEMBER, "nandivelugu", 404, "not_found"),
        ("da_guntur", Role.VILLAGE_ADMIN, "kaza", 201, None),
        ("da_krishna", Role.PANCHAYAT_MEMBER, "nidamanuru", 404, "not_found"),
        ("pm_nandivelugu", Role.PANCHAYAT_MEMBER, "nandivelugu", 403, "permission_denied"),
    ],
)
def test_add_role_assignment(api, world, actor, role, area, status, code):
    response = api(world.users[actor]).post(
        assignments_url(world, "pm_kolakaluru"), {"role": role, "area": world.area_json(area)}, format="json"
    )
    assert_outcome(response, status, code)
    expected = 2 if status == 201 else 1
    assert world.users["pm_kolakaluru"].role_assignments.count() == expected


@pytest.mark.parametrize(
    "actor, area, status, code",
    [
        ("ma_tenali", "nandivelugu", 200, None),
        ("ma_tenali", "kaza", 403, "out_of_area"),
        ("va_kolakaluru", "nandivelugu", 403, "out_of_area"),
        ("da_guntur", "chinakakani", 200, None),
        ("da_krishna", "nidamanuru", 404, "not_found"),
        ("ma_tenali", "tenali", 400, "validation_error"),
    ],
)
def test_reassign_area(api, world, actor, area, status, code):
    response = api(world.users[actor]).patch(
        assignment_url(world, "pm_kolakaluru"), {"area": world.area_json(area)}, format="json"
    )
    assert_outcome(response, status, code)
    assignment = world.users["pm_kolakaluru"].role_assignments.get()
    moved_to = world.ref(area) if status == 200 else world.ref("kolakaluru")
    assert (assignment.area_level, assignment.area_id) == (moved_to.level, moved_to.id)


@pytest.mark.parametrize(
    "actor, status",
    [("va_kolakaluru", 204), ("ma_tenali", 204), ("va_nandivelugu", 404), ("ma_mangalagiri", 404), ("pm_kolakaluru", 403)],
)
def test_remove_assignment(api, world, actor, status):
    response = api(world.users[actor]).delete(assignment_url(world, "pm_kolakaluru"))
    assert response.status_code == status
    assert world.users["pm_kolakaluru"].role_assignments.exists() == (status != 204)


def test_cannot_touch_an_assignment_of_another_user_through_this_one(api, world):
    other = world.users["pm_nandivelugu"].role_assignments.get()
    url = f"{assignments_url(world, 'pm_kolakaluru')}{other.id}/"
    assert api(world.users["ma_tenali"]).delete(url).status_code == 404
    assert RoleAssignment.objects.filter(pk=other.pk).exists()


# --- Creating: validation (the invite email is tested in test_login.py) --------------------------


def create_as(api, world, actor, **body):
    payload = {"name": "Lakshmi", "email": "lakshmi@example.org", "role": Role.PANCHAYAT_MEMBER, "area": world.area_json("kolakaluru")}
    payload.update(body)
    return api(world.users[actor]).post("/api/v1/users/", payload, format="json")


@pytest.mark.parametrize(
    "field, taken_from",
    [("mobile", "mobile"), ("email", "email"), ("username", "username")],
)
def test_mobile_email_and_username_must_be_unique(api, world, field, taken_from):
    response = create_as(api, world, "va_kolakaluru", **{field: getattr(world.users["pm_kaza"], taken_from)})
    assert_outcome(response, 409, "duplicate")
    assert response.json()["error"]["fields"][field]


def test_role_must_match_the_area_level(api, world):
    response = create_as(api, world, "ma_tenali", role=Role.VILLAGE_ADMIN, area=world.area_json("tenali"))
    assert_outcome(response, 400, "validation_error")
    assert "area" in response.json()["error"]["fields"]


def test_area_must_exist_and_be_active(api, world):
    missing = create_as(api, world, "super", area={"level": "VILLAGE", "id": 999999})
    assert_outcome(missing, 400, "validation_error")
    world.areas["kolakaluru"].is_active = False
    world.areas["kolakaluru"].save()
    inactive = create_as(api, world, "super")
    assert_outcome(inactive, 400, "validation_error")


def test_nobody_creates_super_admins_through_the_api(api, world):
    response = create_as(api, world, "super", role=Role.SUPER_ADMIN, area=world.area_json("ap"))
    assert_outcome(response, 403, "permission_denied")


def test_creating_needs_a_login(api, world):
    response = api().post("/api/v1/users/", {}, format="json")
    assert_outcome(response, 401, "not_authenticated")


@pytest.mark.parametrize(
    "field, value",
    [("name", "   "), ("email", "not-an-email"), ("mobile", "12345"), ("username", "a b"), ("role", "KING"), ("area", {"level": "CITY", "id": 1})],
)
def test_create_validation(api, world, field, value):
    response = create_as(api, world, "super", **{field: value})
    assert_outcome(response, 400, "validation_error")
    assert field in response.json()["error"]["fields"]


# --- List filters and counts ---------------------------------------------------------------------


def listed(api, world, actor, query=""):
    return api(world.users[actor]).get(f"/api/v1/users/?page_size=100{query}").json()


def test_list_filters_and_counts(api, world):
    User.objects.filter(pk=world.users["pm_kaza"].pk).update(status=UserStatus.BLOCKED)
    data = listed(api, world, "da_guntur")
    assert data["counts"] == {
        "total": 8,
        "active": 7,
        "blocked": 1,
        "by_role": {"MANDAL_ADMIN": 2, "VILLAGE_ADMIN": 3, "PANCHAYAT_MEMBER": 3},
    }

    members = listed(api, world, "da_guntur", "&role=PANCHAYAT_MEMBER")
    assert {row["name"] for row in members["results"]} == {"pm_kolakaluru", "pm_nandivelugu", "pm_kaza"}
    assert members["counts"]["total"] == 8  # counts ignore the role and status filters

    blocked = listed(api, world, "da_guntur", "&status=blocked")
    assert [row["name"] for row in blocked["results"]] == ["pm_kaza"]

    tenali = listed(api, world, "super", f"&level=MANDAL&area_id={world.areas['tenali'].id}")
    assert {row["name"] for row in tenali["results"]} == {
        "ma_tenali", "va_kolakaluru", "va_nandivelugu", "pm_kolakaluru", "pm_nandivelugu",
    }

    krishna = listed(api, world, "super", f"&district={world.areas['krishna'].id}")
    assert {row["name"] for row in krishna["results"]} == {"da_krishna", "ma_gannavaram", "va_nidamanuru", "pm_nidamanuru"}

    search = listed(api, world, "super", f"&q={world.users['va_kaza'].mobile[-5:]}")
    assert [row["name"] for row in search["results"]] == ["va_kaza"]


def test_a_filter_cannot_widen_the_list(api, world):
    """Asking for Krishna as a Guntur admin returns nobody, not Krishna's users."""
    data = listed(api, world, "da_guntur", f"&district={world.areas['krishna'].id}")
    assert data["results"] == []


def test_list_rows_show_areas_and_allowed_actions(api, world):
    row = next(r for r in listed(api, world, "va_kolakaluru")["results"] if r["name"] == "pm_kolakaluru")
    assert row["role_assignments"][0]["area"]["path"] == "కొలకలూరు · తెనాలి · గుంటూరు"
    assert row["allowed_actions"] == ["edit", "block", "reset_password", "add_assignment", "reassign", "remove_assignment"]

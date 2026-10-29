"""Privilege escalation: nobody gives, resets, blocks or unlocks an equal or higher rank, and
two assignments in different areas never add up to more than each allows. Lists stay inside the areas."""

import pytest
from axes.models import AccessAttempt
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.accounts.roles import Role
from conftest import TEST_PASSWORD, assign, make_bare_post

pytestmark = pytest.mark.django_db


def code(response):
    return response.json()["error"]["code"]


def assignments_url(world, name):
    return f"/api/v1/users/{world.users[name].id}/role-assignments/"


@pytest.mark.parametrize(
    "actor, target, role, area",
    [
        ("da_guntur", "pm_kolakaluru", Role.DISTRICT_ADMIN, "guntur"),  # equal rank
        ("da_guntur", "pm_kolakaluru", Role.SUPER_ADMIN, "ap"),  # higher
        ("ma_tenali", "pm_kolakaluru", Role.MANDAL_ADMIN, "tenali"),
        ("ma_tenali", "pm_kolakaluru", Role.DISTRICT_ADMIN, "guntur"),
        ("va_kolakaluru", "pm_kolakaluru", Role.VILLAGE_ADMIN, "kolakaluru"),
        ("super", "da_guntur", Role.SUPER_ADMIN, "ap"),
    ],
)
def test_no_one_gives_a_role_at_or_above_their_own(api, world, actor, target, role, area):
    before = RoleAssignment.objects.count()
    response = api(world.users[actor]).post(
        assignments_url(world, target), {"role": role, "area": world.area_json(area)}, format="json"
    )
    assert response.status_code in (403, 404)
    if response.status_code == 403:
        assert code(response) == "permission_denied"
    assert RoleAssignment.objects.count() == before


@pytest.mark.parametrize("action", ["block", "reset-password", "unlock", "unblock"])
@pytest.mark.parametrize("actor, target", [
    ("va_kolakaluru", "va_nandivelugu"),
    ("va_kolakaluru", "ma_tenali"),
    ("ma_tenali", "ma_mangalagiri"),
    ("ma_tenali", "da_guntur"),
    ("da_guntur", "da_krishna"),
    ("da_guntur", "super"),
])
def test_no_one_acts_on_an_equal_or_higher_rank(api, world, action, actor, target):
    victim = world.users[target]
    response = api(world.users[actor]).post(f"/api/v1/users/{victim.id}/{action}/", {}, format="json")
    assert response.status_code == 404  # out of reach means invisible
    victim.refresh_from_db()
    assert victim.is_active and victim.check_password(TEST_PASSWORD) and victim.token_version == 0


def test_a_lower_assignment_does_not_reach_a_peer(api, world):
    """Mandal Admin of Tenali, also Village Admin of Kaza: cannot touch the Mangalagiri Mandal Admin."""
    assign(world, "ma_tenali", Role.VILLAGE_ADMIN, "kaza")
    client = api(world.users["ma_tenali"])
    assert client.post(f"/api/v1/users/{world.users['ma_mangalagiri'].id}/block/", {}, format="json").status_code == 404
    # From Kaza they manage Panchayat members of Kaza only.
    assert client.post(f"/api/v1/users/{world.users['pm_kaza'].id}/reset-password/", {}, format="json").status_code == 200
    assert client.post(f"/api/v1/users/{world.users['va_kaza'].id}/block/", {}, format="json").status_code == 404


def test_someone_who_also_holds_a_higher_role_is_out_of_reach(api, world):
    """A Panchayat member who is also Mandal Admin cannot be reset by their Village Admin."""
    assign(world, "pm_kolakaluru", Role.MANDAL_ADMIN, "tenali")
    target = world.users["pm_kolakaluru"]
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json")
    assert response.status_code == 404
    assert api(world.users["da_guntur"]).post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json").status_code == 200


def test_a_switched_off_higher_role_still_protects_the_person(api, world):
    """Deactivating someone's Mandal Admin role does not hand them to Village Admins."""
    assignment = assign(world, "pm_kolakaluru", Role.MANDAL_ADMIN, "tenali")
    assignment.is_active = False
    assignment.save()
    target = world.users["pm_kolakaluru"]
    assert api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/block/", {}, format="json").status_code == 404


def test_a_panchayat_member_cannot_reach_user_management(api, world):
    client = api(world.users["pm_kolakaluru"])
    for method, url in [("get", "/api/v1/users/"), ("post", f"/api/v1/users/{world.users['pm_nandivelugu'].id}/block/")]:
        response = getattr(client, method)(url, {}, format="json")
        assert response.status_code == 403 and code(response) == "permission_denied"


def test_unlock_needs_the_unlock_permission_not_only_reach(api, world):
    from django.contrib.auth.models import Permission

    from apps.accounts.models import RoleGroup

    group = RoleGroup.objects.get(key="VILLAGE_ADMIN").group
    group.permissions.remove(Permission.objects.get(content_type__app_label="accounts", codename="unlock_user"))
    target = world.users["pm_kolakaluru"]
    AccessAttempt.objects.create(
        username=target.username, ip_address="10.0.0.9", user_agent="x", failures_since_start=5,
        attempt_time=timezone.now(), get_data="", post_data="", http_accept="", path_info="/",
    )
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/users/{target.id}/unlock/", {}, format="json")
    assert response.status_code == 403 and code(response) == "permission_denied"
    assert AccessAttempt.objects.exists()
    row = api(world.users["va_kolakaluru"]).get(f"/api/v1/users/{target.id}/").json()
    assert row["locked_out"] is True and "unlock" not in row["allowed_actions"]


# --- Lists stay inside the areas ------------------------------------------------------------------


def make_pending(world, area_key):
    from apps.posts.models import Category, Post

    target = world.ref(area_key)
    return make_bare_post(
        author=world.users["pm_nidamanuru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level="VILLAGE",
        posted_as_area_id=world.ref("nidamanuru").id, category=Category.objects.get(key="announce"),
        title=area_key, body="b", target_level=target.level, target_area_id=target.id, status="PENDING",
        submitted_at=timezone.now(),
    )


def test_two_assignments_see_both_areas_and_nothing_else(api, world):
    """Village Admin of Kolakaluru and of Kaza: queue and post list cover both villages only."""
    assign(world, "va_kolakaluru", Role.VILLAGE_ADMIN, "kaza")
    for key in ("kolakaluru", "kaza", "nandivelugu", "nidamanuru", "tenali"):
        make_pending(world, key)
    client = api(world.users["va_kolakaluru"])
    queue = {row["title"] for row in client.get("/api/v1/review/queue/").json()["results"]}
    assert queue == {"kolakaluru", "kaza"}
    posts = {row["title"] for row in client.get("/api/v1/posts/?status=PENDING").json()["results"]}
    assert posts == {"kolakaluru", "kaza"}


def test_user_list_of_a_two_area_admin(api, world):
    assign(world, "va_kolakaluru", Role.VILLAGE_ADMIN, "kaza")
    names = {row["name"] for row in api(world.users["va_kolakaluru"]).get("/api/v1/users/?page_size=100").json()["results"]}
    assert names == {"pm_kolakaluru", "pm_kaza"}


def test_audit_log_is_for_platform_admins(api, world):
    assert api(world.users["da_guntur"]).get("/api/v1/audit/").status_code == 403
    assert api(world.users["super"]).get("/api/v1/audit/").status_code == 200


def test_new_user_without_assignment_only_listed_for_platform_admins(api, world):
    User.objects.create_user(username="orphan", name="Orphan", password="x")
    assert "Orphan" in {r["name"] for r in api(world.users["super"]).get("/api/v1/users/?page_size=100").json()["results"]}
    assert "Orphan" not in {r["name"] for r in api(world.users["da_guntur"]).get("/api/v1/users/?page_size=100").json()["results"]}

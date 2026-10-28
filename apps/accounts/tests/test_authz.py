"""accounts.authz: permissions per area, ranks, caching, and the role matrix itself."""

import itertools
import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import authz
from apps.accounts.authz import ALL_AREAS, ANYWHERE, PLATFORM
from apps.accounts.models import RoleAssignment, RoleGroup, User
from apps.accounts.role_matrix import ROLE_GROUPS
from apps.accounts.roles import Role
from apps.geography.areas import AreaRef, area_is_within, within_q
from conftest import PARENT, assign, inside

pytestmark = pytest.mark.django_db

ALL_AREAS_KEYS = ["ap", *PARENT.keys()]


# --- The matrix as the brief states it (written out by hand, not read from the dict) ----------

MANAGES = {
    "SUPER_ADMIN": {"DISTRICT_ADMIN", "MANDAL_ADMIN", "VILLAGE_ADMIN", "PANCHAYAT_MEMBER"},
    "DISTRICT_ADMIN": {"MANDAL_ADMIN", "VILLAGE_ADMIN", "PANCHAYAT_MEMBER"},
    "MANDAL_ADMIN": {"VILLAGE_ADMIN", "PANCHAYAT_MEMBER"},
    "VILLAGE_ADMIN": {"PANCHAYAT_MEMBER"},
    "PANCHAYAT_MEMBER": set(),
}
MANAGE_PERMISSION = {
    "DISTRICT_ADMIN": "accounts.manage_district_admin",
    "MANDAL_ADMIN": "accounts.manage_mandal_admin",
    "VILLAGE_ADMIN": "accounts.manage_village_admin",
    "PANCHAYAT_MEMBER": "accounts.manage_panchayat_member",
}
REVIEW = {
    "posts.review_post", "posts.publish_post", "posts.recheck_post", "posts.discard_post",
    "posts.takedown_post", "posts.reverse_decision", "posts.view_review_queue", "posts.mark_urgent",
}
USER_ACTIONS = {"accounts.block_user", "accounts.reset_password", "accounts.reassign_user", "accounts.unlock_user"}
SUPER_ONLY = {
    "accounts.manage_groups", "geography.manage_master_data", "audit.view_audit_log", "audit.export_audit_log",
    "web_admin.access_web_console", "web_admin.view_dashboard", "web_admin.view_reports",
    "notifications.manage_templates", "web_public.manage_contact_messages", "posts.manage_categories",
    "posts.manage_settings", "contacts.manage_contact_types",
    "posts.block_citizen",  # only the Super Admin blocks a citizen's phone (contract section 15)
    "ads.manage_ad",  # Home banner ads (contract section 17)
}


def expected_permissions(key: str, super_admin_can_post: bool = False) -> set[str]:
    # Every official manages important numbers in their own area (contacts plan, decision C1).
    perms = {MANAGE_PERMISSION[role] for role in MANAGES[key]} | {"posts.view_stats", "contacts.manage_contact"}
    # Service requests (contract section 16): everyone sees them; a Panchayat member only those assigned to them.
    perms |= {"service_requests.view_service_requests"}
    if key != "PANCHAYAT_MEMBER":
        perms |= REVIEW | USER_ACTIONS | {"service_requests.manage_service_requests"}
    if key != "SUPER_ADMIN" or super_admin_can_post:
        perms |= {"posts.create_post", "posts.submit_post"}
    if key == "SUPER_ADMIN":
        perms |= SUPER_ONLY
    return perms


@pytest.mark.parametrize("key", list(MANAGES))
def test_groups_hold_exactly_the_brief_s_permissions(db, key):
    group = RoleGroup.objects.get(key=key).group
    held = {f"{app}.{code}" for app, code in group.permissions.values_list("content_type__app_label", "codename")}
    assert held == expected_permissions(key)


def test_super_admin_gets_posting_only_with_the_setting(super_admin_can_post):
    group = RoleGroup.objects.get(key="SUPER_ADMIN").group
    held = {f"{app}.{code}" for app, code in group.permissions.values_list("content_type__app_label", "codename")}
    assert held == expected_permissions("SUPER_ADMIN", super_admin_can_post=True)


def test_role_groups_carry_level_rank_and_manage_permission(db):
    rows = {row.key: row for row in RoleGroup.objects.select_related("group")}
    assert {key: (row.group.name, row.level, row.rank, row.platform_wide) for key, row in rows.items()} == {
        "SUPER_ADMIN": ("Super Admin", "STATE", 0, True),
        "DISTRICT_ADMIN": ("District Admin", "DISTRICT", 1, False),
        "MANDAL_ADMIN": ("Mandal Admin", "MANDAL", 2, False),
        "VILLAGE_ADMIN": ("Village Admin", "VILLAGE", 3, False),
        "PANCHAYAT_MEMBER": ("Panchayat Member", "VILLAGE", 4, False),
    }
    assert rows["SUPER_ADMIN"].manage_permission == ""  # nobody gives it through the API
    assert {key: rows[key].manage_permission for key in MANAGE_PERMISSION} == MANAGE_PERMISSION


def test_no_code_tests_group_names_or_keys():
    """Only the dict, the migration, the sync, display labels, sample data and tests may name a role."""
    root = Path(django_settings.BASE_DIR)
    allowed = {
        "apps/accounts/role_matrix.py",
        "apps/accounts/migrations/0005_roles_to_groups.py",
        "apps/accounts/management/commands/seed_sample_data.py",
    }
    pattern = re.compile(
        r"[\"'](SUPER_ADMIN|DISTRICT_ADMIN|MANDAL_ADMIN|VILLAGE_ADMIN|PANCHAYAT_MEMBER"
        r"|Super Admin|District Admin|Mandal Admin|Village Admin|Panchayat Member)[\"']"
    )
    offenders = []
    for path in [*root.joinpath("apps").rglob("*.py"), *root.joinpath("grama").rglob("*.py")]:
        rel = path.relative_to(root).as_posix()
        if rel in allowed or "/tests/" in rel or "/migrations/0001" in rel or "/migrations/0002" in rel:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert offenders == []


# --- Area containment (Python and SQL agree) --------------------------------------------------


@pytest.mark.parametrize("area_key, container_key", list(itertools.product(ALL_AREAS_KEYS, ALL_AREAS_KEYS)))
def test_area_is_within_matches_the_map(geo, area_key, container_key):
    assert area_is_within(geo.ref(area_key), geo.ref(container_key)) == inside(area_key, container_key)


def test_within_q_selects_exactly_what_area_is_within_says(world):
    super_admin = world.users["super"]
    for key in ALL_AREAS_KEYS:
        RoleAssignment.objects.assign(super_admin, Role.PANCHAYAT_MEMBER, world.ref(key))
    probe = RoleAssignment.objects.filter(user=super_admin).of_role(Role.PANCHAYAT_MEMBER)
    for container_key in ALL_AREAS_KEYS:
        found = {AreaRef(lv, i) for lv, i in probe.filter(within_q(world.ref(container_key))).values_list("area_level", "area_id")}
        assert found == {world.ref(key) for key in ALL_AREAS_KEYS if inside(key, container_key)}, container_key


# --- can() and areas_for() ----------------------------------------------------------------------


@pytest.mark.parametrize("target", ALL_AREAS_KEYS)
def test_can_follows_the_assignment_area(world, target):
    user = world.users["ma_tenali"]
    assert authz.can(user, "posts.publish_post", world.ref(target)) == inside(target, "tenali")


def test_super_admin_covers_the_whole_platform(world):
    user = world.users["super"]
    assert authz.can(user, "posts.publish_post", world.ref("kanuru"))
    assert authz.can(user, "posts.publish_post", AreaRef("STATE", 12345))
    assert authz.can(user, "web_admin.view_dashboard", PLATFORM)
    assert authz.areas_for(user, "posts.review_post") is ALL_AREAS
    assert not authz.can(world.users["da_guntur"], "posts.publish_post", PLATFORM)


def test_permission_the_group_lacks_is_never_granted(world):
    assert not authz.can(world.users["pm_kolakaluru"], "posts.publish_post", world.ref("kolakaluru"))
    assert not authz.can(world.users["da_guntur"], "web_admin.access_web_console", ANYWHERE)


def test_blocked_users_and_pending_password_changes_hold_nothing(world):
    user = world.users["va_kolakaluru"]
    user.must_change_password = True
    assert not authz.can(user, "posts.publish_post", world.ref("kolakaluru"))
    assert authz.can(user, "posts.publish_post", world.ref("kolakaluru"), pending_change_ok=True)
    user.must_change_password = False
    user.is_active = False
    assert not authz.can(user, "posts.publish_post", world.ref("kolakaluru"))


def test_inactive_assignments_grant_nothing(world):
    RoleAssignment.objects.filter(user=world.users["va_kolakaluru"]).update(is_active=False)
    user = User.objects.get(pk=world.users["va_kolakaluru"].pk)
    assert authz.grants(user) == []
    assert authz.areas_for(user, "posts.review_post") == []


def test_two_assignments_in_different_areas_do_not_leak(world):
    """Mandal Admin of Tenali and Panchayat Member of Kaza: admin powers stay in Tenali."""
    assign(world, "ma_tenali", Role.PANCHAYAT_MEMBER, "kaza")
    user = User.objects.get(pk=world.users["ma_tenali"].pk)
    kaza, tenali_village = world.ref("kaza"), world.ref("kolakaluru")
    assert authz.can(user, "posts.create_post", kaza)
    assert not authz.can(user, "posts.publish_post", kaza)
    assert not authz.can(user, "accounts.block_user", kaza)
    assert authz.can(user, "posts.publish_post", tenali_village)
    assert authz.areas_for(user, "posts.publish_post") == [world.ref("tenali")]
    assert sorted(authz.areas_for(user, "posts.create_post")) == sorted([world.ref("tenali"), kaza])
    # Posting to Kaza happens as a Panchayat member: it waits for review.
    assert authz.best_grant(user, "posts.create_post", kaza).key == "PANCHAYAT_MEMBER"


def test_highest_rank_wins_where_two_assignments_overlap(world):
    assign(world, "va_kolakaluru", Role.PANCHAYAT_MEMBER, "kolakaluru")
    user = User.objects.get(pk=world.users["va_kolakaluru"].pk)
    assert authz.best_grant(user, "posts.create_post", world.ref("kolakaluru")).key == "VILLAGE_ADMIN"


def test_setting_off_wins_over_a_stale_sync(world, settings, super_admin_can_post):
    """SUPER_ADMIN_CAN_POST is checked again on every request, not only by the sync."""
    settings.SUPER_ADMIN_CAN_POST = False  # changed without re-running the sync
    user = User.objects.get(pk=world.users["super"].pk)
    assert not authz.can(user, "posts.create_post", world.ref("ap"))
    settings.SUPER_ADMIN_CAN_POST = True
    user = User.objects.get(pk=world.users["super"].pk)
    assert authz.can(user, "posts.create_post", world.ref("ap"))


# --- Ranks and management ------------------------------------------------------------------------


def roles():
    return authz.roles()


@pytest.mark.parametrize("actor, role, area, allowed", [
    ("super", "DISTRICT_ADMIN", "krishna", True),
    ("super", "SUPER_ADMIN", "ap", False),
    ("da_guntur", "DISTRICT_ADMIN", "guntur", False),
    ("da_guntur", "MANDAL_ADMIN", "tenali", True),
    ("da_guntur", "MANDAL_ADMIN", "gannavaram", False),
    ("ma_tenali", "MANDAL_ADMIN", "tenali", False),
    ("ma_tenali", "VILLAGE_ADMIN", "kolakaluru", True),
    ("va_kolakaluru", "VILLAGE_ADMIN", "kolakaluru", False),
    ("va_kolakaluru", "PANCHAYAT_MEMBER", "kolakaluru", True),
    ("pm_kolakaluru", "PANCHAYAT_MEMBER", "kolakaluru", False),
])
def test_can_assign_only_lower_ranks_inside_the_area(world, actor, role, area, allowed):
    assert authz.can_assign(world.users[actor], roles()[role], world.ref(area)) == allowed


def test_rank_check_holds_even_if_a_group_got_a_manage_permission_by_mistake(world):
    """A Village Admin group given manage_village_admin still cannot make peers."""
    from django.contrib.auth.models import Permission

    group = RoleGroup.objects.get(key="VILLAGE_ADMIN").group
    group.permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="manage_village_admin"))
    user = User.objects.get(pk=world.users["va_kolakaluru"].pk)
    assert "accounts.manage_village_admin" in authz.permissions(user)
    assert not authz.can_assign(user, roles()["VILLAGE_ADMIN"], world.ref("kolakaluru"))


def test_can_act_on_needs_every_assignment_in_reach(world):
    assign(world, "pm_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")
    target = User.objects.prefetch_related("role_assignments__group__role_group").get(pk=world.users["pm_kolakaluru"].pk)
    assert not authz.can_act_on(world.users["va_kolakaluru"], "accounts.reset_password", target)
    assert authz.can_act_on(world.users["da_guntur"], "accounts.reset_password", target)


def test_nobody_acts_on_themselves(world):
    assert not authz.can_act_on(world.users["super"], "accounts.block_user", world.users["super"])


def test_users_without_roles_are_left_to_platform_admins(world):
    orphan = User.objects.create_user(username="orphan", name="Orphan", password="x")
    assert authz.can_act_on(world.users["super"], "accounts.block_user", orphan)
    assert not authz.can_act_on(world.users["da_guntur"], "accounts.block_user", orphan)


# --- Group membership follows assignments ------------------------------------------------------


def test_group_membership_follows_active_assignments(world):
    user = world.users["pm_kolakaluru"]
    key = lambda: sorted(user.groups.values_list("role_group__key", flat=True))  # noqa: E731
    assert key() == ["PANCHAYAT_MEMBER"]
    second = assign(world, "pm_kolakaluru", Role.PANCHAYAT_MEMBER, "kaza")
    village_admin = assign(world, "pm_kolakaluru", Role.VILLAGE_ADMIN, "kaza")
    assert key() == ["PANCHAYAT_MEMBER", "VILLAGE_ADMIN"]
    second.delete()
    assert key() == ["PANCHAYAT_MEMBER", "VILLAGE_ADMIN"]  # one Panchayat assignment is left
    RoleAssignment.objects.filter(user=user).of_role(Role.PANCHAYAT_MEMBER).get().delete()
    assert key() == ["VILLAGE_ADMIN"]
    village_admin.is_active = False
    village_admin.save()
    assert key() == []
    village_admin.is_active = True
    village_admin.save()
    assert key() == ["VILLAGE_ADMIN"]


# --- Caching: no query per check -------------------------------------------------------------------


def test_grants_load_in_two_queries_and_are_cached(world):
    user = User.objects.get(pk=world.users["ma_tenali"].pk)
    with CaptureQueriesContext(connection) as first:
        authz.grants(user)
    assert len(first) == 2
    targets = [world.ref(key) for key in ("kolakaluru", "nandivelugu", "kaza", "tenali")]
    authz.prime(user, targets)
    with CaptureQueriesContext(connection) as later:
        for target in targets:
            for perm in ("posts.publish_post", "accounts.block_user", "posts.create_post"):
                authz.can(user, perm, target)
        authz.areas_for(user, "posts.review_post")
        authz.permissions(user)
    assert len(later) == 0


@pytest.mark.parametrize("extra", [0, 12])
def test_user_list_queries_do_not_grow_with_the_page(api, world, extra, django_assert_max_num_queries):
    for index in range(extra):
        user = User.objects.create_user(username=f"member{index}", name=f"Member {index}", password="x")
        RoleAssignment.objects.assign(user, Role.PANCHAYAT_MEMBER, world.ref("kaza" if index % 2 else "kolakaluru"))
    client = api(world.users["da_guntur"])
    with django_assert_max_num_queries(20):
        response = client.get("/api/v1/users/?page_size=100")
    assert response.status_code == 200


@pytest.mark.parametrize("extra", [0, 12])
def test_post_list_queries_do_not_grow_with_the_page(api, world, extra, django_assert_max_num_queries):
    from apps.posts.models import Category, Post

    category = Category.objects.first()
    for index in range(extra):
        target = world.ref(["kolakaluru", "nandivelugu", "kaza", "chinakakani"][index % 4])
        Post.objects.create(
            author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER",
            posted_as_level="VILLAGE", posted_as_area_id=world.ref("kolakaluru").id, category=category,
            title=f"Post {index}", body="b", target_level=target.level, target_area_id=target.id, status="PENDING",
        )
    with django_assert_max_num_queries(25):
        response = api(world.users["da_guntur"]).get("/api/v1/posts/?page_size=50")
    assert response.status_code == 200
    assert response.json()["count"] == extra


def test_the_groups_are_plain_django_groups(db):
    assert set(Group.objects.values_list("name", flat=True)) >= {spec["name"] for spec in ROLE_GROUPS.values()}

"""The authorization matrix through the API: every group x every permission x where.

For each group one official on the map acts (conftest.OFFICIALS). For each
permission a probe calls the endpoint that needs it, against an area in a
position relative to the official's own area:

    same     the official's own area
    below    an area inside it
    parent   the area above it (for user and role probes: somewhere under the
             parent but outside the official's area)
    sibling  the next area at the same level under the same parent
    other    an area in the other district
    platform endpoints with no area (Super Admin screens)

Expected: allowed exactly when the group holds the permission (role_matrix.py,
with SUPER_ADMIN_CAN_POST off) and the area lies inside the official's area,
or the group is platform-wide. test_authz.py checks the dict itself against
the brief, so this file checks that the API enforces it everywhere.
"""

from __future__ import annotations

import uuid

import pytest
from axes.models import AccessAttempt
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.accounts.role_matrix import ALL_PERMISSIONS, ROLE_GROUPS
from apps.accounts.sync import wanted_permissions
from apps.citizens.models import CitizenDevice
from apps.posts.models import CITIZEN_ROLE, Category, Post, PostStatus
from apps.service_requests.models import ServiceRequest
from conftest import MAP, PARENT, TEST_PASSWORD, inside

pytestmark = pytest.mark.django_db

ACTOR = {
    "SUPER_ADMIN": "super",
    "DISTRICT_ADMIN": "da_guntur",
    "MANDAL_ADMIN": "ma_tenali",
    "VILLAGE_ADMIN": "va_kolakaluru",
    "PANCHAYAT_MEMBER": "pm_kolakaluru",
}
HOME = {"super": "ap", "da_guntur": "guntur", "ma_tenali": "tenali", "va_kolakaluru": "kolakaluru", "pm_kolakaluru": "kolakaluru"}
POSITIONS = {
    "ap": {"same": "ap", "below": "guntur"},
    "guntur": {"same": "guntur", "below": "tenali", "parent": "ap", "sibling": "krishna"},
    "tenali": {"same": "tenali", "below": "kolakaluru", "parent": "guntur", "sibling": "mangalagiri", "other": "gannavaram"},
    "kolakaluru": {"same": "kolakaluru", "parent": "tenali", "sibling": "nandivelugu", "other": "nidamanuru"},
}

LEVELS = ["STATE", "DISTRICT", "MANDAL", "VILLAGE"]
ALL_KEYS = ["ap", *PARENT]


def level_of(key: str) -> str:
    depth, current = 0, key
    while current in PARENT:
        current, depth = PARENT[current], depth + 1
    return LEVELS[depth]


def area_at(level: str, position: str, where: str, home: str) -> str | None:
    """An area at `level` inside `where`; for the parent position, outside the official's own area."""
    found = [key for key in ALL_KEYS if level_of(key) == level and inside(key, where)]
    if position == "parent":
        found = [key for key in found if not inside(key, home)]
    return found[0] if found else None


ROLE_LEVEL = {key: spec["level"] for key, spec in ROLE_GROUPS.items()}
MANAGE_ROLE = {spec["manage_permission"]: key for key, spec in ROLE_GROUPS.items() if spec["manage_permission"]}
USER_PROBES = {"accounts.block_user", "accounts.reset_password", "accounts.unlock_user", "accounts.reassign_user"}
PLATFORM_PROBES = {
    "accounts.manage_groups": "admin",
    "geography.manage_master_data": "/api/v1/geo/tree/",
    "audit.view_audit_log": "/api/v1/audit/",
    "audit.export_audit_log": "/api/v1/audit/export/?format=csv",
    "web_admin.access_web_console": "session",
    "web_admin.view_dashboard": "/api/v1/admin/dashboard/",
    "web_admin.view_reports": "/api/v1/admin/reports/summary/",
    "notifications.manage_templates": "/api/v1/admin/notification-templates/",
    "web_public.manage_contact_messages": "/api/v1/admin/contact-messages/",
    "contacts.manage_contact_types": "/api/v1/admin/contact-types/",
    "posts.manage_categories": "/api/v1/admin/categories/",
    "posts.manage_settings": "/api/v1/admin/settings/",
    "ads.manage_ad": "/api/v1/admin/ads/",
    "service_requests.manage_services": "/api/v1/admin/services/",
}


#: Probes whose target is always a village: a member's village, or a citizen post's village.
VILLAGE_PROBES = USER_PROBES | {
    "posts.block_citizen",
    "service_requests.view_service_requests",
    "service_requests.manage_service_requests",
}


def target_for(perm: str, position: str, home: str) -> str | None:
    where = POSITIONS[home][position]
    if perm in MANAGE_ROLE:
        return area_at(ROLE_LEVEL[MANAGE_ROLE[perm]], position, where, home)
    if perm in VILLAGE_PROBES:
        return area_at("VILLAGE", position, where, home)
    return where


CASES = []
for _key, _name in ACTOR.items():
    for _perm in ALL_PERMISSIONS:
        if _perm in PLATFORM_PROBES:
            CASES.append((_key, _perm, "platform"))
            continue
        for _position in POSITIONS[HOME[_name]]:
            if target_for(_perm, _position, HOME[_name]) is not None:
                CASES.append((_key, _perm, _position))


# --- Probes -----------------------------------------------------------------------------------------

def outcome(response, ok=(200, 201)) -> bool:
    if response.status_code in ok:
        return True
    assert response.status_code in (403, 404), (response.status_code, response.content[:300])
    return False


def post_at(world, area_key: str, status: str, *, author: str = "pm_nidamanuru", decided_by_role: str = "") -> Post:
    target = world.ref(area_key)
    now = timezone.now()
    decided = status not in (PostStatus.DRAFT, PostStatus.PENDING)
    return Post.objects.create(
        author=world.users[author],
        posted_as_role="PANCHAYAT_MEMBER",
        posted_as_level="VILLAGE",
        posted_as_area_id=world.ref("nidamanuru").id,
        category=Category.objects.get(key="announce"),
        title="Probe",
        body="Probe text.",
        target_level=target.level,
        target_area_id=target.id,
        status=status,
        submitted_at=now,
        published_at=now if status == PostStatus.PUBLISHED else None,
        decided_at=now if decided else None,
        decided_by_role=decided_by_role,
    )


def citizen_post_at(world, village_key: str) -> Post:
    """A pending citizen post (contract section 15) in that village, from a fresh phone."""
    village = world.ref(village_key)
    phone = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex, platform="android", village_id=village.id, nonce=uuid.uuid4().hex
    )
    return Post.objects.create(
        citizen=phone,
        posted_as_role=CITIZEN_ROLE,
        posted_as_level=village.level,
        posted_as_area_id=village.id,
        category=Category.objects.get(key="announce"),
        title="Citizen probe",
        body="Probe text.",
        target_level=village.level,
        target_area_id=village.id,
        status=PostStatus.PENDING,
        submitted_at=timezone.now(),
    )


def service_request_at(world, village_key: str, assigned_to=None) -> ServiceRequest:
    """A NEW service request (contract section 16) from a fresh phone in that village."""
    village = world.ref(village_key)
    phone = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex, platform="android", village_id=village.id, nonce=uuid.uuid4().hex
    )
    return ServiceRequest.objects.create(
        service_id="aadhaar", name="Probe", phone="9876543210", address="Probe street", village_id=village.id,
        device=phone, assigned_to=assigned_to,
    )


def member_at(world, village_key: str) -> User:
    user = User.objects.create_user(username="probe.member", name="Probe Member", password=TEST_PASSWORD)
    RoleAssignment.objects.assign(user, "PANCHAYAT_MEMBER", world.ref(village_key))
    return User.objects.get(pk=user.pk)


def review(client, post, action, **extra):
    return client.post(f"/api/v1/posts/{post.id}/review/", {"action": action, "comment": "probe", **extra}, format="json")


def probe(world, client, actor: User, perm: str, area_key: str | None) -> bool:
    if perm in PLATFORM_PROBES:
        how = PLATFORM_PROBES[perm]
        if how == "session":
            response = APIClient().post(
                "/api/v1/auth/session/login/", {"username": actor.username, "password": TEST_PASSWORD}, format="json"
            )
            return outcome(response)
        if how == "admin":
            User.objects.filter(pk=actor.pk).update(is_staff=True)
            browser = Client()
            browser.force_login(actor)
            return outcome(browser.get("/django-admin/auth/group/"))
        return outcome(client.get(how))

    area = world.ref(area_key)
    if perm == "posts.create_post":
        body = {"category": "announce", "title": "T", "body": "B", "target": world.area_json(area_key), "submit": False}
        return outcome(client.post("/api/v1/posts/", body, format="json"))
    if perm == "posts.submit_post":
        draft = post_at(world, area_key, PostStatus.DRAFT, author=actor.username)
        return outcome(client.post(f"/api/v1/posts/{draft.id}/submit/", {}, format="json"))
    if perm == "posts.review_post":
        return outcome(client.get(f"/api/v1/posts/{post_at(world, area_key, PostStatus.PENDING).id}/"))
    if perm == "posts.publish_post":
        return outcome(review(client, post_at(world, area_key, PostStatus.PENDING), "publish"))
    if perm == "posts.recheck_post":
        return outcome(review(client, post_at(world, area_key, PostStatus.PENDING), "recheck"))
    if perm == "posts.discard_post":
        return outcome(review(client, post_at(world, area_key, PostStatus.PENDING), "discard"))
    if perm == "posts.takedown_post":
        return outcome(review(client, post_at(world, area_key, PostStatus.PUBLISHED), "unpublish"))
    if perm == "posts.reverse_decision":
        decided = post_at(world, area_key, PostStatus.DISCARDED, decided_by_role="PANCHAYAT_MEMBER")
        return outcome(review(client, decided, "reverse", to_status="PUBLISHED"))
    if perm == "posts.view_review_queue":
        pending = post_at(world, area_key, PostStatus.PENDING)
        response = client.get("/api/v1/review/queue/?page_size=100")
        if not outcome(response):
            return False
        return str(pending.id) in {row["id"] for row in response.json()["results"]}
    if perm == "posts.mark_urgent":
        published = post_at(world, area_key, PostStatus.PUBLISHED)
        return outcome(client.post(f"/api/v1/posts/{published.id}/urgent/", {"is_urgent": True}, format="json"))
    if perm == "posts.view_stats":
        return outcome(client.get(f"/api/v1/stats/?level={area.level}&area_id={area.id}"))
    if perm == "service_requests.view_service_requests":
        # Assigned to the actor: a Panchayat member sees exactly those, inside their own area.
        request = service_request_at(world, area_key, assigned_to=actor)
        response = client.get("/api/v1/service-requests/?page_size=100")
        if not outcome(response):
            return False
        return request.id in {row["id"] for row in response.json()["results"]}
    if perm == "service_requests.manage_service_requests":
        request = service_request_at(world, area_key)
        return outcome(client.patch(f"/api/v1/service-requests/{request.id}/", {"status": "APPROVED"}, format="json"))
    if perm == "posts.block_citizen":
        citizen_post = citizen_post_at(world, area_key)
        body = {"comment": "probe", "days": 7}
        return outcome(client.post(f"/api/v1/posts/{citizen_post.id}/block-citizen/", body, format="json"))
    if perm == "contacts.manage_contact":
        if area.level == "STATE":
            body = {"type": "health", "name": "Ambulance", "phone": "108"}
        else:
            body = {"type": "official", "name": "Probe office", "phone": "08644222333"}
        return outcome(client.post("/api/v1/admin/contacts/", {**body, "area": world.area_json(area_key)}, format="json"))
    if perm in MANAGE_ROLE:
        body = {"name": "Probe", "username": "probe.new", "role": MANAGE_ROLE[perm], "area": world.area_json(area_key)}
        return outcome(client.post("/api/v1/users/", body, format="json"))
    target = member_at(world, area_key)
    if perm == "accounts.block_user":
        return outcome(client.post(f"/api/v1/users/{target.id}/block/", {}, format="json"))
    if perm == "accounts.reset_password":
        return outcome(client.post(f"/api/v1/users/{target.id}/reset-password/", {}, format="json"))
    if perm == "accounts.unlock_user":
        AccessAttempt.objects.create(
            username=target.username, ip_address="10.0.0.9", user_agent="probe", failures_since_start=5,
            attempt_time=timezone.now(), get_data="", post_data="", http_accept="", path_info="/api/v1/auth/login/",
        )
        return outcome(client.post(f"/api/v1/users/{target.id}/unlock/", {}, format="json"))
    if perm == "accounts.reassign_user":
        assignment = target.role_assignments.get()
        url = f"/api/v1/users/{target.id}/role-assignments/{assignment.id}/"
        return outcome(client.patch(url, {"area": world.area_json(area_key)}, format="json"))
    raise AssertionError(f"No probe for {perm}")


def expected(key: str, perm: str, area_key: str | None, home: str) -> bool:
    spec = ROLE_GROUPS[key]
    held = wanted_permissions(spec)
    if perm not in held:
        return False
    if perm in PLATFORM_PROBES:
        return spec["platform_wide"]
    if perm in USER_PROBES and "accounts.manage_panchayat_member" not in held:
        return False  # the target is a Panchayat member: the actor must manage them too
    return spec["platform_wide"] or inside(area_key, home)


def test_every_permission_has_a_probe_and_every_group_is_tried():
    assert {perm for _key, perm, _pos in CASES} == set(ALL_PERMISSIONS)
    assert {key for key, _perm, _pos in CASES} == set(ROLE_GROUPS)
    positions = {pos for _key, _perm, pos in CASES}
    assert positions == {"same", "below", "parent", "sibling", "other", "platform"}


@pytest.mark.parametrize("key, perm, position", CASES)
def test_matrix(api, world, key, perm, position):
    name = ACTOR[key]
    home = HOME[name]
    area_key = None if position == "platform" else target_for(perm, position, home)
    actor = world.users[name]
    assert probe(world, api(actor), actor, perm, area_key) == expected(key, perm, area_key, home)


def test_matrix_covers_the_map():
    """The positions are what the docstring says, worked out from conftest.MAP."""
    assert set(MAP) == {"guntur", "krishna"}
    assert POSITIONS["tenali"]["other"] not in ("guntur",) and not inside(POSITIONS["tenali"]["other"], "guntur")
    assert not inside(POSITIONS["kolakaluru"]["other"], "guntur")
    for home, places in POSITIONS.items():
        assert inside(places["same"], home)
        if "below" in places:
            assert inside(places["below"], home) and places["below"] != home
        for position in ("parent", "sibling", "other"):
            if position in places:
                assert not inside(places[position], home)

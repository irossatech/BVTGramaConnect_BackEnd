"""A block stops the phone's new posts only; it changes no post (owner's request of 2026-11-21).

From 2026-11-20 to 2026-11-21 a block moved the phone's pending posts to BLOCKED; posts/0013 reopened them.
- Blocking leaves pending posts PENDING, in the review queue, for the reviewers to decide as usual; a
  published one stays published. No post.blocked entry, no "discarded" push to the phone.
- "Discard all" still discards; the review API still has no "block" action.
- Posts BLOCKED before the change (made here through posts.services directly) keep their reason in
  last_review, are seen by the same reviewers, show as DISCARDED to the citizen, and only a Super Admin
  can bring them back (owner's decision of 2026-11-21): to PENDING, PUBLISHED or DISCARDED, never RECHECK.
Contract sections 10 and 15.
"""

import pytest

from apps.audit.models import AuditLog
from apps.notifications.push import LocmemPushGateway
from apps.posts import services as post_services
from apps.posts.models import Post, PostStatus

from .conftest import POSTS

pytestmark = pytest.mark.django_db

LIST = "/api/v1/posts/"


def block(api, world, post, comment="Spam posts", days=7):
    return api(world.users["super"]).post(f"{LIST}{post.id}/block-citizen/", {"comment": comment, "days": days}, format="json")


def listed(client, status=""):
    data = client.get(LIST, {"status": status, "page_size": 100} if status else {"page_size": 100}).json()
    return {row["id"] for row in data["results"]}, data["status_counts"]


@pytest.fixture
def three(world, phone, django_capture_on_commit_callbacks):
    """One phone in Kolakaluru with three pending posts."""
    LocmemPushGateway.outbox.clear()
    citizen = phone("kolakaluru", fcm_token="fcm-citizen", language="en")
    with django_capture_on_commit_callbacks(execute=True):
        ids = [citizen.post(title=title).json()["id"] for title in ("మొదటి", "రెండవ", "మూడవ")]
    posts = [Post.objects.get(pk=pk) for pk in ids]
    return citizen, posts


@pytest.fixture
def blocked_phone(api, world, three, django_capture_on_commit_callbacks):
    """The third post published, then the phone blocked from the first."""
    citizen, posts = three
    api(world.users["va_kolakaluru"]).post(f"{LIST}{posts[2].id}/review/", {"action": "publish"}, format="json")
    LocmemPushGateway.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert block(api, world, posts[0]).status_code == 200
    return citizen, posts


@pytest.fixture
def old_blocked(world, three):
    """Two posts closed by a block before 2026-11-21, as that code did it."""
    _citizen, posts = three
    for post in posts[:2]:
        post_services.review(world.users["super"], post, "block", "Spam posts")
    return posts


def test_a_block_changes_no_post(blocked_phone):
    _citizen, posts = blocked_phone
    statuses = [Post.objects.get(pk=post.pk).status for post in posts]
    assert statuses == [PostStatus.PENDING, PostStatus.PENDING, PostStatus.PUBLISHED]
    assert not AuditLog.objects.filter(action="post.blocked").exists()
    assert AuditLog.objects.filter(action="citizen.blocked").count() == 1


def test_the_waiting_posts_stay_in_the_review_queue_and_can_be_decided(api, world, blocked_phone):
    _citizen, posts = blocked_phone
    reviewer = api(world.users["va_kolakaluru"])
    queue = {row["id"] for row in reviewer.get("/api/v1/review/queue/", {"page_size": 100}).json()["results"]}
    assert {str(posts[0].id), str(posts[1].id)} <= queue
    assert reviewer.post(f"{LIST}{posts[0].id}/review/", {"action": "publish"}, format="json").status_code == 200
    discarded = reviewer.post(f"{LIST}{posts[1].id}/review/", {"action": "discard", "comment": "x"}, format="json")
    assert discarded.status_code == 200 and discarded.json()["status"] == "DISCARDED"


def test_the_phone_gets_no_push_from_the_block(blocked_phone):
    assert not [m for m in LocmemPushGateway.outbox if m.token == "fcm-citizen"]


def test_the_blocked_phone_cannot_post_but_its_old_posts_are_untouched(blocked_phone):
    citizen, _posts = blocked_phone
    response = citizen.post(title="నాల్గవ")
    assert response.status_code == 403 and response.json()["error"]["code"] == "citizen_blocked"
    assert Post.objects.filter(status=PostStatus.PENDING).count() == 2


def test_block_is_not_a_review_action(api, world, three):
    _citizen, posts = three
    response = api(world.users["super"]).post(f"{LIST}{posts[0].id}/review/", {"action": "block", "comment": "x"}, format="json")
    assert response.status_code == 400
    assert Post.objects.get(pk=posts[0].pk).status == PostStatus.PENDING


def test_discard_all_still_discards(api, world, three):
    _citizen, posts = three
    response = api(world.users["super"]).post(f"{LIST}{posts[0].id}/discard-citizen-posts/", {"comment": "Spam"}, format="json")
    assert response.status_code == 200
    assert {Post.objects.get(pk=post.pk).status for post in posts} == {PostStatus.DISCARDED}


# --- Posts a block closed before 2026-11-21 ----------------------------------------------------------


def test_an_old_blocked_post_keeps_its_reason_in_last_review(api, world, old_blocked):
    row = api(world.users["super"]).get(f"{LIST}{old_blocked[0].id}/").json()
    assert row["status"] == "BLOCKED"
    assert row["last_review"]["action"] == "BLOCK" and row["last_review"]["comment"] == "Spam posts"
    assert row["last_review"]["actor"]["role"] == "SUPER_ADMIN"


@pytest.mark.parametrize("viewer, sees", [
    ("super", True), ("da_guntur", True), ("ma_tenali", True), ("va_kolakaluru", True),
    ("va_nandivelugu", False), ("ma_mangalagiri", False), ("da_krishna", False), ("pm_kolakaluru", False),
])
def test_who_sees_an_old_blocked_post_is_unchanged(api, world, old_blocked, viewer, sees):
    client = api(world.users[viewer])
    assert (str(old_blocked[0].id) in listed(client, "BLOCKED")[0]) is sees
    assert client.get(f"{LIST}{old_blocked[0].id}/").status_code == (200 if sees else 404)


@pytest.mark.parametrize("to_status", ["PENDING", "PUBLISHED", "DISCARDED"])
def test_the_super_admin_brings_an_old_blocked_post_back(api, world, old_blocked, to_status):
    client = api(world.users["super"])
    assert "reverse" in client.get(f"{LIST}{old_blocked[0].id}/").json()["allowed_actions"]
    response = client.post(
        f"{LIST}{old_blocked[0].id}/review/", {"action": "reverse", "to_status": to_status, "comment": "mistake"}, format="json"
    )
    assert response.status_code == 200 and response.json()["status"] == to_status
    assert old_blocked[0].review_actions.filter(action="REVERSE", from_status="BLOCKED", to_status=to_status).exists()
    assert AuditLog.objects.filter(action="post.reversed", target_id=str(old_blocked[0].id)).exists()


def test_an_old_blocked_post_never_goes_to_recheck(api, world, old_blocked):
    response = api(world.users["super"]).post(
        f"{LIST}{old_blocked[0].id}/review/", {"action": "reverse", "to_status": "RECHECK", "comment": "x"}, format="json"
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "invalid_transition"
    assert Post.objects.get(pk=old_blocked[0].pk).status == PostStatus.BLOCKED


@pytest.mark.parametrize("reviewer", ["da_guntur", "ma_tenali", "va_kolakaluru"])
def test_other_admins_cannot_bring_it_back(api, world, old_blocked, reviewer):
    client = api(world.users[reviewer])
    assert "reverse" not in client.get(f"{LIST}{old_blocked[0].id}/").json()["allowed_actions"]
    response = client.post(
        f"{LIST}{old_blocked[0].id}/review/", {"action": "reverse", "to_status": "PENDING", "comment": "x"}, format="json"
    )
    assert response.status_code == 403
    publish = client.post(f"{LIST}{old_blocked[1].id}/review/", {"action": "publish"}, format="json")
    assert publish.status_code == 409 and publish.json()["error"]["code"] == "already_decided"


def test_the_citizen_sees_an_old_blocked_post_as_discarded_with_the_reason(three, old_blocked):
    citizen, _posts = three
    mine = citizen.client.get(POSTS).json()
    rows = {row["id"]: row for row in (mine["results"] if isinstance(mine, dict) else mine)}
    assert rows[str(old_blocked[0].id)]["status"] == "DISCARDED" and rows[str(old_blocked[0].id)]["reason"] == "Spam posts"

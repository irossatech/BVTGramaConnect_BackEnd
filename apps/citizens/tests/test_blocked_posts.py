"""A citizen's pending posts become BLOCKED when the Super Admin blocks the phone (decision of 2026-11-20).

Before, a block discarded them, so they sat under the console's Rejected tab with every discarded post.
Now they get their own status, BLOCKED, and the console's Blocked tab (`GET /posts/?status=BLOCKED`).
- Only a block makes it: "Discard all" and a reviewer's discard still give DISCARDED.
- A published post of the phone stays published.
- BLOCKED is final: a later reviewer gets 409 already_decided; the review API has no "block" action.
- The citizen's own API still says DISCARDED (the app knows no BLOCKED), with the block comment as the
  reason, and the phone gets the same "discarded" push as before.
- Who sees a blocked post is unchanged: the reviewers of its area, as for a discarded one.
Contract sections 10 and 15.
"""

import pytest

from apps.audit.models import AuditLog
from apps.notifications.push import LocmemPushGateway
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
    """One phone in Kolakaluru with two pending posts and one published post."""
    LocmemPushGateway.outbox.clear()
    citizen = phone("kolakaluru", fcm_token="fcm-citizen", language="en")
    with django_capture_on_commit_callbacks(execute=True):
        ids = [citizen.post(title=title).json()["id"] for title in ("మొదటి", "రెండవ", "మూడవ")]
    posts = [Post.objects.get(pk=pk) for pk in ids]
    return citizen, posts


@pytest.fixture
def blocked(api, world, three, django_capture_on_commit_callbacks):
    citizen, posts = three
    api(world.users["va_kolakaluru"]).post(f"{LIST}{posts[2].id}/review/", {"action": "publish"}, format="json")
    LocmemPushGateway.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert block(api, world, posts[0]).status_code == 200
    return citizen, posts


def test_a_block_moves_the_pending_posts_to_blocked(blocked):
    _citizen, posts = blocked
    statuses = [Post.objects.get(pk=post.pk).status for post in posts]
    assert statuses == [PostStatus.BLOCKED, PostStatus.BLOCKED, PostStatus.PUBLISHED]


def test_they_are_in_the_blocked_tab_not_in_rejected(api, world, blocked):
    _citizen, posts = blocked
    client = api(world.users["super"])
    ids, counts = listed(client, "BLOCKED")
    assert ids == {str(posts[0].id), str(posts[1].id)}
    assert counts["BLOCKED"] == 2 and counts["DISCARDED"] == 0
    assert listed(client, "DISCARDED,UNPUBLISHED")[0] == set()  # the console's Rejected tab
    assert client.get(f"{LIST}{posts[0].id}/").json()["status"] == "BLOCKED"


def test_each_blocked_post_has_its_history_and_audit(blocked):
    _citizen, posts = blocked
    for post in posts[:2]:
        action = post.review_actions.get(action="BLOCK")
        assert (action.from_status, action.to_status, action.comment) == ("PENDING", "BLOCKED", "Spam posts")
    assert AuditLog.objects.filter(action="post.blocked").count() == 2
    assert AuditLog.objects.filter(action="citizen.blocked").count() == 1
    assert not AuditLog.objects.filter(action="post.discarded").exists()


@pytest.mark.parametrize("viewer, sees", [
    ("super", True), ("da_guntur", True), ("ma_tenali", True), ("va_kolakaluru", True),
    ("va_nandivelugu", False), ("ma_mangalagiri", False), ("da_krishna", False), ("pm_kolakaluru", False),
])
def test_who_sees_a_blocked_post_is_unchanged(api, world, blocked, viewer, sees):
    _citizen, posts = blocked
    client = api(world.users[viewer])
    assert (str(posts[0].id) in listed(client, "BLOCKED")[0]) is sees
    assert client.get(f"{LIST}{posts[0].id}/").status_code == (200 if sees else 404)


@pytest.mark.parametrize("action", ["publish", "discard"])
def test_a_blocked_post_is_final_for_reviewers(api, world, blocked, action):
    _citizen, posts = blocked
    response = api(world.users["va_kolakaluru"]).post(
        f"{LIST}{posts[1].id}/review/", {"action": action, "comment": "x"}, format="json"
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_decided"
    assert Post.objects.get(pk=posts[1].pk).status == PostStatus.BLOCKED


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


def test_the_citizen_still_sees_discarded_with_the_reason(blocked):
    citizen, posts = blocked
    mine = citizen.client.get(POSTS).json()
    rows = {row["id"]: row for row in (mine["results"] if isinstance(mine, dict) else mine)}
    assert rows[str(posts[0].id)]["status"] == "DISCARDED" and rows[str(posts[0].id)]["reason"] == "Spam posts"
    assert rows[str(posts[2].id)]["status"] == "PUBLISHED"


def test_the_phone_gets_the_discarded_push(blocked):
    _citizen, posts = blocked
    to_phone = [m for m in LocmemPushGateway.outbox if m.token == "fcm-citizen"]
    assert sorted((m.data["post_id"], m.data["type"]) for m in to_phone) == sorted(
        (str(post.id), "POST_DISCARDED") for post in posts[:2]
    )

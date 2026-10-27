"""Who sees and decides a citizen post, and blocking the phone. Contract section 15.

A post from Kolakaluru goes to the Village Admin of Kolakaluru, the Mandal
Admin of Tenali, the District Admin of Guntur and the Super Admin, nobody else.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.citizens.models import CitizenDevice
from apps.notifications.models import Notification, NotificationKind
from apps.posts.models import Post, PostStatus
from apps.posts.services import escalate_overdue

from .conftest import POSTS

pytestmark = pytest.mark.django_db

REVIEWERS = {"va_kolakaluru", "ma_tenali", "da_guntur", "super"}
NOT_REVIEWERS = {"va_nandivelugu", "va_kaza", "va_nidamanuru", "ma_mangalagiri", "ma_gannavaram", "da_krishna",
                 "pm_kolakaluru", "pm_nandivelugu"}


def code(response):
    return response.json()["error"]["code"]


@pytest.fixture
def citizen_post(world, phone, django_capture_on_commit_callbacks):
    citizen = phone("kolakaluru")
    with django_capture_on_commit_callbacks(execute=True):
        post_id = citizen.post().json()["id"]
    return citizen, Post.objects.get(pk=post_id)


def queue_ids(api, user):
    return {row["id"] for row in api(user).get("/api/v1/review/queue/?page_size=100").json()["results"]}


def test_it_reaches_the_village_mandal_district_and_super_admin_only(api, world, citizen_post):
    _citizen, post = citizen_post
    for name in REVIEWERS:
        assert str(post.id) in queue_ids(api, world.users[name]), name
    for name in NOT_REVIEWERS:
        response = api(world.users[name]).get("/api/v1/review/queue/?page_size=100")
        assert response.status_code == 403 or str(post.id) not in {r["id"] for r in response.json()["results"]}, name
        assert api(world.users[name]).get(f"/api/v1/posts/{post.id}/").status_code == 404, name


def test_the_area_reviewers_are_alerted_at_once(world, citizen_post):
    alerted = {n.user.username for n in Notification.objects.filter(kind=NotificationKind.REVIEW_PENDING)}
    assert alerted == {"va_kolakaluru", "ma_tenali", "da_guntur"}  # as for a Panchayat member's post


def test_without_a_decision_it_climbs_to_the_super_admin(world, citizen_post, django_capture_on_commit_callbacks):
    _citizen, post = citizen_post
    hours = 25
    for level in ("MANDAL", "DISTRICT", "STATE"):
        with django_capture_on_commit_callbacks(execute=True):
            escalate_overdue(now=timezone.now() + timedelta(hours=hours))
        post.refresh_from_db()
        assert post.escalation_level == level
        hours += 25
    escalated = {n.user.username for n in Notification.objects.filter(kind=NotificationKind.ESCALATION)}
    assert "super" in escalated


def test_officials_see_who_wrote_it_only_as_a_citizen(api, world, citizen_post):
    citizen, post = citizen_post
    data = api(world.users["va_kolakaluru"]).get(f"/api/v1/posts/{post.id}/").json()
    assert data["author"] == {"id": None, "name": "", "role": "CITIZEN"}
    assert data["posted_as"]["role"] == "CITIZEN"
    assert data["citizen"] == {"device_id": citizen.device_id, "blocked": False, "posts_total": 1, "posts_discarded": 0}
    assert data["allowed_actions"] == ["publish", "discard", "set_urgent", "block_citizen"]


def test_officials_posts_have_no_citizen(api, world):
    body = {"category": "announce", "title": "T", "body": "B", "target": world.area_json("kolakaluru")}
    data = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json").json()
    assert data["citizen"] is None and "block_citizen" not in data["allowed_actions"]


def test_publishing_puts_it_in_the_feed_as_citizen(api, world, citizen_post):
    citizen, post = citizen_post
    response = api(world.users["ma_tenali"]).post(f"/api/v1/posts/{post.id}/review/", {"action": "publish"}, format="json")
    assert response.status_code == 200 and response.json()["status"] == "PUBLISHED"
    feed = APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}").json()["results"]
    row = next(item for item in feed if item["id"] == str(post.id))
    assert row["source_role"] == "CITIZEN"
    assert citizen.device_id not in str(row) and citizen.token not in str(row)
    mine = citizen.client.get(POSTS).json()[0]
    assert mine["status"] == "PUBLISHED" and mine["decided_at"]


def test_the_first_decision_wins(api, world, citizen_post):
    _citizen, post = citizen_post
    assert api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{post.id}/review/", {"action": "publish"}, format="json").status_code == 200
    late = api(world.users["da_guntur"]).post(f"/api/v1/posts/{post.id}/review/", {"action": "discard", "comment": "x"}, format="json")
    assert late.status_code == 409 and code(late) == "already_decided"


def test_a_discard_reason_reaches_the_citizen(api, world, citizen_post):
    citizen, post = citizen_post
    api(world.users["va_kolakaluru"]).post(
        f"/api/v1/posts/{post.id}/review/", {"action": "discard", "comment": "ఇది ఇప్పటికే పరిష్కరించబడింది"}, format="json"
    )
    mine = citizen.client.get(POSTS).json()[0]
    assert mine["status"] == "DISCARDED" and mine["reason"] == "ఇది ఇప్పటికే పరిష్కరించబడింది"


def test_a_citizen_post_is_never_sent_for_recheck(api, world, citizen_post):
    _citizen, post = citizen_post
    client = api(world.users["va_kolakaluru"])
    response = client.post(f"/api/v1/posts/{post.id}/review/", {"action": "recheck", "comment": "fix"}, format="json")
    assert response.status_code == 409 and code(response) == "invalid_transition"
    client.post(f"/api/v1/posts/{post.id}/review/", {"action": "discard", "comment": "spam"}, format="json")
    reverse = api(world.users["ma_tenali"]).post(
        f"/api/v1/posts/{post.id}/review/", {"action": "reverse", "comment": "c", "to_status": "RECHECK"}, format="json"
    )
    assert reverse.status_code == 409 and code(reverse) == "invalid_transition"
    back = api(world.users["ma_tenali"]).post(
        f"/api/v1/posts/{post.id}/review/", {"action": "reverse", "comment": "real issue", "to_status": "PUBLISHED"}, format="json"
    )
    assert back.status_code == 200 and back.json()["status"] == "PUBLISHED"


# --- Blocking ---------------------------------------------------------------------------------------


def block(api, world, name, post, comment="Spam posts"):
    return api(world.users[name]).post(f"/api/v1/posts/{post.id}/block-citizen/", {"comment": comment}, format="json")


def test_blocking_stops_the_phone_and_discards_its_pending_posts(api, world, phone, citizen_post):
    citizen, post = citizen_post
    second = Post.objects.get(pk=citizen.post(title="రెండవ").json()["id"])
    published = Post.objects.get(pk=citizen.post(title="మూడవ").json()["id"])
    api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{published.id}/review/", {"action": "publish"}, format="json")

    response = block(api, world, "ma_tenali", post)
    assert response.status_code == 200
    assert response.json()["citizen"]["blocked"] is True
    assert response.json()["allowed_actions"][-1] == "unblock_citizen"
    statuses = dict(Post.objects.filter(citizen_id=citizen.device_id).values_list("title", "status"))
    assert statuses == {post.title: PostStatus.DISCARDED, "రెండవ": PostStatus.DISCARDED, "మూడవ": PostStatus.PUBLISHED}
    assert second.review_actions.filter(action="DISCARD", comment="Spam posts").exists()
    device = CitizenDevice.objects.get(pk=citizen.device_id)
    assert device.blocked and device.blocked_by == world.users["ma_tenali"] and device.block_reason == "Spam posts"
    entry = AuditLog.objects.get(action="citizen.blocked")
    assert entry.actor == world.users["ma_tenali"] and entry.data == {"post_id": str(post.id), "comment": "Spam posts"}

    refused = citizen.post(title="మళ్ళీ")
    assert refused.status_code == 403 and code(refused) == "citizen_blocked"


def test_blocking_needs_a_reason(api, world, citizen_post):
    _citizen, post = citizen_post
    response = block(api, world, "va_kolakaluru", post, comment=" ")
    assert response.status_code == 400 and "comment" in response.json()["error"]["fields"]
    assert not CitizenDevice.objects.get().blocked


@pytest.mark.parametrize("name, status", [("va_nandivelugu", 404), ("da_krishna", 404), ("pm_kolakaluru", 404)])
def test_only_the_villages_reviewers_can_block(api, world, citizen_post, name, status):
    _citizen, post = citizen_post
    assert block(api, world, name, post).status_code == status
    assert not CitizenDevice.objects.get().blocked


def test_unblock_lets_the_phone_post_again(api, world, citizen_post):
    citizen, post = citizen_post
    block(api, world, "va_kolakaluru", post)
    response = api(world.users["super"]).post(f"/api/v1/posts/{post.id}/unblock-citizen/", {}, format="json")
    assert response.status_code == 200 and response.json()["citizen"]["blocked"] is False
    assert "block_citizen" in response.json()["allowed_actions"]
    assert citizen.post(title="మళ్ళీ").status_code == 201
    assert AuditLog.objects.filter(action="citizen.unblocked", actor=world.users["super"]).count() == 1


def test_officials_posts_cannot_be_blocked(api, world):
    body = {"category": "announce", "title": "T", "body": "B", "target": world.area_json("kolakaluru")}
    post_id = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json").json()["id"]
    response = api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{post_id}/block-citizen/", {"comment": "x"}, format="json")
    assert response.status_code == 400

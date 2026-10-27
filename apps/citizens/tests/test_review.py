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
    assert data["citizen"] == {
        "device_id": citizen.device_id, "blocked": False, "blocked_until": None, "posts_total": 1, "posts_discarded": 0,
    }
    assert data["allowed_actions"] == ["publish", "discard", "set_urgent"]  # blocking is the Super Admin's
    super_actions = api(world.users["super"]).get(f"/api/v1/posts/{post.id}/").json()["allowed_actions"]
    assert super_actions == ["publish", "discard", "set_urgent", "block_citizen", "discard_citizen_posts"]


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


def block(api, world, post, name="super", comment="Spam posts", days=7):
    body = {"comment": comment, "days": days}
    return api(world.users[name]).post(f"/api/v1/posts/{post.id}/block-citizen/", body, format="json")


def test_the_super_admin_blocks_for_some_days_and_pending_posts_go(api, world, phone, citizen_post):
    citizen, post = citizen_post
    second = Post.objects.get(pk=citizen.post(title="రెండవ").json()["id"])
    published = Post.objects.get(pk=citizen.post(title="మూడవ").json()["id"])
    api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{published.id}/review/", {"action": "publish"}, format="json")

    response = block(api, world, post, days=7)
    assert response.status_code == 200
    summary = response.json()["citizen"]
    assert summary["blocked"] is True and summary["blocked_until"].endswith("+05:30")
    assert "unblock_citizen" in response.json()["allowed_actions"]
    statuses = dict(Post.objects.filter(citizen_id=citizen.device_id).values_list("title", "status"))
    assert statuses == {post.title: PostStatus.DISCARDED, "రెండవ": PostStatus.DISCARDED, "మూడవ": PostStatus.PUBLISHED}
    assert second.review_actions.filter(action="DISCARD", comment="Spam posts").exists()
    device = CitizenDevice.objects.get(pk=citizen.device_id)
    assert device.blocked and device.blocked_by == world.users["super"] and device.block_reason == "Spam posts"
    assert timedelta(days=6, hours=23) < device.blocked_until - device.blocked_at <= timedelta(days=7)
    entry = AuditLog.objects.get(action="citizen.blocked")
    assert entry.actor == world.users["super"] and entry.data["days"] == 7 and entry.data["comment"] == "Spam posts"


def test_a_blocked_citizen_is_told_why_and_until_when(api, world, citizen_post):
    citizen, post = citizen_post
    block(api, world, post, comment="తప్పుడు సమాచారం", days=3)
    refused = citizen.post(title="మళ్ళీ")
    assert refused.status_code == 403
    error = refused.json()["error"]
    assert error["code"] == "citizen_blocked" and error["reason"] == "తప్పుడు సమాచారం"
    assert "తప్పుడు సమాచారం" in error["message"] and error["blocked_until"].endswith("+05:30")
    me = citizen.client.get("/api/v1/citizen/me/").json()
    assert me["blocked"] is True and me["block_reason"] == "తప్పుడు సమాచారం" and me["blocked_until"] == error["blocked_until"]


def test_a_block_ends_by_itself(api, world, citizen_post):
    citizen, post = citizen_post
    block(api, world, post, days=1)
    assert citizen.post(title="మళ్ళీ").status_code == 403
    CitizenDevice.objects.update(blocked_until=timezone.now() - timedelta(minutes=1))
    assert citizen.post(title="మళ్ళీ").status_code == 201
    assert citizen.client.get("/api/v1/citizen/me/").json()["blocked"] is False


def test_a_block_survives_reinstalling_the_app(api, world, phone, django_capture_on_commit_callbacks):
    first = phone(android_id="a1b2c3d4e5f60718")
    with django_capture_on_commit_callbacks(execute=True):
        post = Post.objects.get(pk=first.post().json()["id"])
    block(api, world, post, days=30)
    reinstalled = phone(android_id="a1b2c3d4e5f60718", ip="10.7.7.7")  # uninstall, install, new token
    assert reinstalled.token != first.token
    refused = reinstalled.post(title="కొత్త ఇన్‌స్టాల్")
    assert refused.status_code == 403 and code(refused) == "citizen_blocked"
    assert reinstalled.client.get("/api/v1/citizen/me/").json()["blocked"] is True
    other_phone = phone(android_id="ffff000011112222", ip="10.7.7.8")
    assert other_phone.post().status_code == 201


@pytest.mark.parametrize("days", [0, 366, None])
def test_the_block_length_is_one_to_365_days(api, world, citizen_post, days):
    _citizen, post = citizen_post
    body = {"comment": "x"} if days is None else {"comment": "x", "days": days}
    response = api(world.users["super"]).post(f"/api/v1/posts/{post.id}/block-citizen/", body, format="json")
    assert response.status_code == 400 and "days" in response.json()["error"]["fields"]


def test_blocking_needs_a_reason(api, world, citizen_post):
    _citizen, post = citizen_post
    response = block(api, world, post, comment=" ")
    assert response.status_code == 400 and "comment" in response.json()["error"]["fields"]
    assert not CitizenDevice.objects.get().blocked


@pytest.mark.parametrize(
    "name, status",
    [("va_kolakaluru", 403), ("ma_tenali", 403), ("da_guntur", 403), ("va_nandivelugu", 404), ("pm_kolakaluru", 404)],
)
def test_only_the_super_admin_blocks(api, world, citizen_post, name, status):
    """Reviewers of the village may discard, not block (403); others cannot even see the post (404)."""
    _citizen, post = citizen_post
    assert block(api, world, post, name=name).status_code == status
    assert not CitizenDevice.objects.get().blocked


def test_the_super_admin_unblocks(api, world, citizen_post):
    citizen, post = citizen_post
    block(api, world, post)
    assert api(world.users["da_guntur"]).post(f"/api/v1/posts/{post.id}/unblock-citizen/", {}, format="json").status_code == 403
    response = api(world.users["super"]).post(f"/api/v1/posts/{post.id}/unblock-citizen/", {}, format="json")
    assert response.status_code == 200 and response.json()["citizen"]["blocked"] is False
    assert "block_citizen" in response.json()["allowed_actions"]
    assert citizen.post(title="మళ్ళీ").status_code == 201
    assert AuditLog.objects.filter(action="citizen.unblocked", actor=world.users["super"]).count() == 1


def test_officials_posts_cannot_be_blocked(api, world):
    body = {"category": "announce", "title": "T", "body": "B", "target": world.area_json("kolakaluru")}
    post_id = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json").json()["id"]
    response = api(world.users["super"]).post(
        f"/api/v1/posts/{post_id}/block-citizen/", {"comment": "x", "days": 1}, format="json"
    )
    assert response.status_code == 400


# --- Discard all of one phone's posts ---------------------------------------------------------------


def test_the_super_admin_discards_all_pending_posts_of_a_phone(api, world, phone, citizen_post):
    citizen, post = citizen_post
    citizen.post(title="రెండవ")
    citizen.post(title="మూడవ")
    other = phone(ip="10.6.6.6")
    other_post = Post.objects.get(pk=other.post(title="వేరే").json()["id"])
    url = f"/api/v1/posts/{post.id}/discard-citizen-posts/"
    assert api(world.users["va_kolakaluru"]).post(url, {"comment": "spam"}, format="json").status_code == 403
    assert api(world.users["super"]).post(url, {}, format="json").status_code == 400
    response = api(world.users["super"]).post(url, {"comment": "spam"}, format="json")
    assert response.status_code == 200 and response.json() == {"discarded": 3}
    assert set(Post.objects.filter(citizen_id=citizen.device_id).values_list("status", flat=True)) == {PostStatus.DISCARDED}
    other_post.refresh_from_db()
    assert other_post.status == PostStatus.PENDING  # another phone is untouched
    assert not CitizenDevice.objects.get(pk=citizen.device_id).blocked  # discarding is not blocking
    assert AuditLog.objects.get(action="citizen.posts_discarded").data["discarded"] == 3


# --- Separate queue ---------------------------------------------------------------------------------


def test_citizen_posts_have_their_own_queue(api, world, citizen_post):
    _citizen, post = citizen_post
    body = {"category": "announce", "title": "Official", "body": "B", "target": world.area_json("kolakaluru")}
    official = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json").json()["id"]
    client = api(world.users["va_kolakaluru"])
    everything = client.get("/api/v1/review/queue/").json()
    assert {row["id"] for row in everything["results"]} == {str(post.id), official}
    assert everything["source_counts"] == {"official": 1, "citizen": 1}
    citizen_tab = client.get("/api/v1/review/queue/?source=citizen").json()
    assert [row["id"] for row in citizen_tab["results"]] == [str(post.id)]
    assert citizen_tab["source_counts"] == {"official": 1, "citizen": 1}  # counts for both tabs either way
    assert [row["id"] for row in client.get("/api/v1/review/queue/?source=official").json()["results"]] == [official]
    assert [row["id"] for row in client.get("/api/v1/posts/?source=citizen").json()["results"]] == [str(post.id)]
    assert client.get("/api/v1/review/queue/?source=nobody").status_code == 400


# --- The citizen hears the decision (CP4) -------------------------------------------------------------


def test_the_citizen_gets_a_push_when_the_post_is_decided(api, world, phone, django_capture_on_commit_callbacks):
    from apps.notifications.push import LocmemPushGateway

    LocmemPushGateway.outbox.clear()
    citizen = phone(fcm_token="fcm-citizen", language="en")
    with django_capture_on_commit_callbacks(execute=True):
        published = Post.objects.get(pk=citizen.post(title="Broken pipe").json()["id"])
        discarded = Post.objects.get(pk=citizen.post(title="Spam").json()["id"])
    LocmemPushGateway.outbox.clear()
    reviewer = api(world.users["va_kolakaluru"])
    with django_capture_on_commit_callbacks(execute=True):
        reviewer.post(f"/api/v1/posts/{published.id}/review/", {"action": "publish"}, format="json")
        reviewer.post(f"/api/v1/posts/{discarded.id}/review/", {"action": "discard", "comment": "Duplicate"}, format="json")
    to_phone = [m for m in LocmemPushGateway.outbox if m.token == "fcm-citizen"]
    assert len(to_phone) == 2
    assert [(m.data["post_id"], m.data["type"]) for m in to_phone] == [
        (str(published.id), "POST_PUBLISHED"),
        (str(discarded.id), "POST_DISCARDED"),
    ]
    assert to_phone[0].body == "Broken pipe" and to_phone[1].body == "Spam"
    assert not Notification.objects.filter(post__in=[published, discarded], kind__in=["POST_PUBLISHED", "POST_DISCARDED"]).exists()


def test_no_push_without_a_token(api, world, phone, django_capture_on_commit_callbacks):
    from apps.notifications.push import LocmemPushGateway

    citizen = phone()  # sent no fcm_token
    with django_capture_on_commit_callbacks(execute=True):
        post = Post.objects.get(pk=citizen.post().json()["id"])
    LocmemPushGateway.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{post.id}/review/", {"action": "publish"}, format="json")
    assert all(m.token is None for m in LocmemPushGateway.outbox)  # only the village topic push

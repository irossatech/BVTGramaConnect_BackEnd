"""The Super Admin's blocked citizens (owner's request of 2026-11-21; contract section 15).

GET /admin/citizens/blocked/ lists one row per blocked phone (every install), from its latest block,
?active=1 (blocked now, the default) or 0 (ended), with counts. GET /admin/citizens/{id}/blocks/ lists
every block of a phone; POST /admin/citizens/{id}/unblock/ lifts one without a post. A block whose time
is up is marked ended once by the job (citizen.block_ended). GET /posts/?citizen= lists one phone's posts.
Only the Super Admin (posts.block_citizen); no phone number, IP or Android ID in any answer.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.citizens import services
from apps.citizens.models import CitizenBlock, CitizenDevice
from apps.posts.models import Post, PostStatus

pytestmark = pytest.mark.django_db

BLOCKED = "/api/v1/admin/citizens/blocked/"
LIST = "/api/v1/posts/"
OFFICIALS = ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"]


def block(api, world, post, comment="Spam posts", days=7, who="super"):
    return api(world.users[who]).post(f"{LIST}{post.id}/block-citizen/", {"comment": comment, "days": days}, format="json")


def first_post(citizen, title="తాగునీరు"):
    return Post.objects.get(pk=citizen.post(title=title).json()["id"])


def rows(client, **params):
    data = client.get(BLOCKED, params).json()
    return data["results"], data["counts"]


@pytest.fixture
def sa(api, world):
    return api(world.users["super"])


def test_a_blocked_phone_is_listed_with_its_block(api, world, phone, sa):
    citizen = phone("kolakaluru")
    post = first_post(citizen)
    assert block(api, world, post, days=7).status_code == 200
    results, counts = rows(sa)
    assert counts == {"active": 1, "ended": 0}
    [row] = results
    assert row["id"] == citizen.device_id
    assert row["village"]["id"] == world.areas["kolakaluru"].id
    assert row["blocked"] is True and row["reason"] == "Spam posts"
    assert row["blocked_by"] == {"id": str(world.users["super"].id), "name": world.users["super"].name, "role": "SUPER_ADMIN"}
    assert row["blocked_until"].endswith("+05:30") and row["ended_at"] is None and row["ended_by"] is None
    assert (row["posts_total"], row["posts_discarded"], row["posts_blocked"]) == (1, 0, 0)
    assert row["latest_post_id"] == str(post.id)


def test_a_phone_blocked_from_a_published_post_with_nothing_waiting_is_listed(api, world, phone, sa):
    citizen = phone("kolakaluru")
    post = first_post(citizen)
    api(world.users["va_kolakaluru"]).post(f"{LIST}{post.id}/review/", {"action": "publish"}, format="json")
    block(api, world, post)
    results, _counts = rows(sa)
    assert [row["id"] for row in results] == [citizen.device_id]
    assert Post.objects.get(pk=post.pk).status == PostStatus.PUBLISHED


def test_two_installs_of_one_phone_are_one_row(api, world, phone, sa):
    old = phone("kolakaluru", android_id="same-phone-id")
    new = phone("kolakaluru", ip="10.20.30.41", android_id="same-phone-id")
    first_post(old)
    later = first_post(new, "రెండవ")
    block(api, world, later)
    results, counts = rows(sa)
    assert counts["active"] == 1
    [row] = results
    assert row["id"] == old.device_id  # the phone's first install: a stable id
    assert row["posts_total"] == 2 and row["latest_post_id"] == str(later.id)


def test_blocking_again_shows_the_latest_reason_and_ends_the_first(api, world, phone, sa):
    citizen = phone("kolakaluru")
    post = first_post(citizen)
    block(api, world, post, comment="first", days=1)
    block(api, world, post, comment="second", days=30)
    [row], counts = rows(sa)
    assert row["reason"] == "second" and counts == {"active": 1, "ended": 0}
    history = sa.get(f"/api/v1/admin/citizens/{citizen.device_id}/blocks/").json()
    assert [entry["reason"] for entry in history] == ["second", "first"]
    assert history[1]["ended_by"]["role"] == "SUPER_ADMIN" and history[1]["blocked"] is False


def test_active_and_ended(api, world, phone, sa):
    running = phone("kolakaluru")
    finished = phone("nandivelugu", ip="10.20.30.42")
    block(api, world, first_post(running), days=7)
    block(api, world, first_post(finished), days=1)
    CitizenBlock.objects.filter(device_id=finished.device_id).update(blocked_until=timezone.now() - timedelta(minutes=1))
    active, counts = rows(sa)
    ended, _ = rows(sa, active=0)
    assert counts == {"active": 1, "ended": 1}
    assert [row["id"] for row in active] == [running.device_id]
    assert [row["id"] for row in ended] == [finished.device_id]
    assert ended[0]["blocked"] is False and ended[0]["ended_at"] and ended[0]["ended_by"] is None
    assert sa.get(BLOCKED, {"active": "x"}).status_code == 400


def test_district_narrows_the_list(api, world, phone, sa):
    guntur = phone("kolakaluru")
    block(api, world, first_post(guntur))
    assert rows(sa, district=world.areas["guntur"].id)[1]["active"] == 1
    assert rows(sa, district=world.areas["krishna"].id)[1]["active"] == 0


def test_unblock_from_the_list_without_a_post(api, world, phone, sa):
    citizen = phone("kolakaluru")
    block(api, world, first_post(citizen))
    response = sa.post(f"/api/v1/admin/citizens/{citizen.device_id}/unblock/")
    assert response.status_code == 200 and response.json()["blocked"] is False
    assert citizen.post(title="మళ్ళీ").status_code == 201
    entry = CitizenBlock.objects.get()
    assert entry.ended_at is not None and entry.ended_by == world.users["super"]
    assert AuditLog.objects.filter(action="citizen.unblocked").count() == 1
    assert rows(sa)[1] == {"active": 0, "ended": 1}
    ended = rows(sa, active=0)[0][0]
    assert ended["reason"] == "Spam posts"  # an unblock keeps the reason for the record
    assert ended["ended_by"]["role"] == "SUPER_ADMIN"


def test_unblock_from_a_post_also_ends_the_record(api, world, phone, sa):
    citizen = phone("kolakaluru")
    post = first_post(citizen)
    block(api, world, post)
    assert sa.post(f"{LIST}{post.id}/unblock-citizen/").status_code == 200
    assert CitizenBlock.objects.get().ended_by == world.users["super"]


def test_the_job_ends_a_finished_block_once(api, world, phone, sa):
    citizen = phone("kolakaluru")
    block(api, world, first_post(citizen), days=1)
    later = timezone.now() + timedelta(days=1, minutes=1)
    assert services.end_finished_blocks(now=later) == 1
    assert services.end_finished_blocks(now=later) == 0
    entry = CitizenBlock.objects.get()
    assert entry.ended_at == entry.blocked_until and entry.ended_by is None
    [audit] = AuditLog.objects.filter(action="citizen.block_ended")
    assert audit.actor is None and audit.target_id == citizen.device_id


def test_the_job_skips_running_and_unblocked_blocks(api, world, phone, sa):
    running = phone("kolakaluru")
    lifted = phone("nandivelugu", ip="10.20.30.42")
    block(api, world, first_post(running), days=7)
    block(api, world, first_post(lifted), days=1)
    sa.post(f"/api/v1/admin/citizens/{lifted.device_id}/unblock/")
    assert services.end_finished_blocks(now=timezone.now() + timedelta(days=2)) == 0
    assert not AuditLog.objects.filter(action="citizen.block_ended").exists()


def test_posts_of_one_citizen(api, world, phone, sa):
    old = phone("kolakaluru", android_id="same-phone-id")
    new = phone("kolakaluru", ip="10.20.30.41", android_id="same-phone-id")
    other = phone("kolakaluru", ip="10.20.30.43")
    mine = {str(first_post(old).id), str(first_post(new, "రెండవ").id)}
    first_post(other, "వేరే")
    listed = {row["id"] for row in sa.get(LIST, {"citizen": old.device_id, "page_size": 100}).json()["results"]}
    assert listed == mine
    assert sa.get(LIST, {"citizen": "not-an-id"}).status_code == 400
    assert sa.get(LIST, {"citizen": "00000000-0000-4000-8000-000000000000"}).status_code == 404


@pytest.mark.parametrize("official", OFFICIALS)
def test_only_the_super_admin(api, world, phone, official):
    citizen = phone("kolakaluru")
    block(api, world, first_post(citizen))
    client = api(world.users[official])
    for response in (
        client.get(BLOCKED),
        client.get(f"/api/v1/admin/citizens/{citizen.device_id}/blocks/"),
        client.post(f"/api/v1/admin/citizens/{citizen.device_id}/unblock/"),
        client.get(LIST, {"citizen": citizen.device_id}),
    ):
        assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"
    assert CitizenDevice.objects.get(pk=citizen.device_id).blocked


def test_not_logged_in(api, world):
    assert api(None).get(BLOCKED).status_code == 401


def test_unknown_phone_is_404(sa):
    missing = "00000000-0000-4000-8000-000000000000"
    assert sa.get(f"/api/v1/admin/citizens/{missing}/blocks/").status_code == 404
    assert sa.post(f"/api/v1/admin/citizens/{missing}/unblock/").status_code == 404


def test_no_personal_data_in_the_answer(api, world, phone, sa):
    citizen = phone("kolakaluru", android_id="android-secret-id")
    block(api, world, first_post(citizen))
    text = sa.get(BLOCKED).content.decode()
    device = CitizenDevice.objects.get(pk=citizen.device_id)
    for secret in ("android-secret-id", device.android_id_hash, device.registered_ip_hash, "10.20.30.40", device.token_hash):
        assert secret not in text

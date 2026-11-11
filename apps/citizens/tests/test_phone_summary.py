"""The phone summary on citizen posts, worked out for a whole page at once (audit finding BE-DB-005).

The rules (contract section 15), from which the expected values here are worked out:
- "The phone" is every install with the same Android ID hash; an install without one is a phone by itself.
- posts_total and posts_discarded count the posts of every install of the phone.
- blocked / blocked_until: the phone's longest running block; one without an end date is the longest.
- The Super Admin's button says unblock_citizen while the phone is blocked, else block_citizen.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.citizens.models import CitizenDevice
from apps.citizens.services import active_block
from apps.posts.models import Category, PostStatus
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


def install(world, android_hash="", **block):
    return CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id,
        nonce=uuid.uuid4().hex, android_id_hash=android_hash, **block,
    )


def citizen_post(world, device, status=PostStatus.PENDING):
    ref = world.ref("kolakaluru")
    return make_bare_post(
        citizen=device, posted_as_role="CITIZEN", posted_as_level=ref.level, posted_as_area_id=ref.id,
        category=Category.objects.first(), title="పైపు", body="b", target_level=ref.level, target_area_id=ref.id,
        status=status, submitted_at=timezone.now(),
    )


def queue(api, world, name="super"):
    response = api(world.users[name]).get("/api/v1/review/queue/?source=citizen&page_size=100")
    assert response.status_code == 200
    return {row["citizen"]["device_id"]: row for row in response.json()["results"]}


def test_every_install_of_a_phone_counts(api, world):
    first, second = install(world, "phone-a"), install(world, "phone-a")  # a reinstall
    citizen_post(world, first)
    citizen_post(world, first, PostStatus.DISCARDED)
    citizen_post(world, second, PostStatus.PUBLISHED)
    citizen_post(world, second, PostStatus.DISCARDED)
    iphone = install(world)  # no Android ID: a phone by itself
    citizen_post(world, iphone)
    other_iphone = install(world)
    citizen_post(world, other_iphone, PostStatus.DISCARDED)

    rows = queue(api, world)  # pending posts only: phone A's came from its first install
    summary = rows[str(first.pk)]["citizen"]
    assert (summary["posts_total"], summary["posts_discarded"]) == (4, 2)
    summary = rows[str(iphone.pk)]["citizen"]
    assert (summary["posts_total"], summary["posts_discarded"]) == (1, 0)  # not mixed with the other iPhone


def test_blocks_running_expired_and_without_an_end(api, world):
    now = timezone.now()
    week = now + timedelta(days=7)
    blocked = install(world, "phone-b", blocked_at=now, blocked_until=week, block_reason="spam")
    citizen_post(world, install(world, "phone-b"))  # a new install of a blocked phone
    citizen_post(world, install(world, "phone-c", blocked_at=now - timedelta(days=9), blocked_until=now - timedelta(days=2)))
    forever = install(world, "phone-d", blocked_at=now - timedelta(days=60))  # an older block: no end date
    install(world, "phone-d", blocked_at=now, blocked_until=week)
    citizen_post(world, forever)

    by_phone = {CitizenDevice.objects.get(pk=key).android_id_hash: row for key, row in queue(api, world).items()}
    assert by_phone["phone-b"]["citizen"]["blocked"] is True
    assert by_phone["phone-b"]["citizen"]["blocked_until"] is not None
    assert "unblock_citizen" in by_phone["phone-b"]["allowed_actions"]
    assert by_phone["phone-c"]["citizen"]["blocked"] is False  # the block ended
    assert "block_citizen" in by_phone["phone-c"]["allowed_actions"]
    summary = by_phone["phone-d"]["citizen"]
    assert (summary["blocked"], summary["blocked_until"]) == (True, None)  # the block without an end wins
    # The posting check and the citizen's own message pick the same block, on every database.
    assert active_block(forever).blocked_until is None
    assert active_block(blocked).blocked_until is not None


def test_a_reviewer_who_cannot_block_sees_the_summary_but_not_the_button(api, world):
    device = install(world, "phone-e", blocked_at=timezone.now())
    citizen_post(world, device)
    row = queue(api, world, "da_guntur")[str(device.pk)]
    assert row["citizen"]["blocked"] is True
    assert "block_citizen" not in row["allowed_actions"] and "unblock_citizen" not in row["allowed_actions"]


def queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.parametrize(
    "name, url",
    [
        ("super", "/api/v1/review/queue/?source=citizen&page_size=100"),
        ("da_guntur", "/api/v1/review/queue/?page_size=100"),
        ("super", "/api/v1/posts/?page_size=100"),
    ],
)
def test_queries_do_not_grow_with_citizen_posts(api, world, name, url):
    client = api(world.users[name])
    citizen_post(world, install(world, "first-phone"))
    one = queries(client, url)
    for index in range(10):  # ten more phones, some blocked, some with two installs
        block = {"blocked_at": timezone.now()} if index % 3 == 0 else {}
        device = install(world, f"phone-{index}", **block)
        install(world, f"phone-{index}")
        citizen_post(world, device)
    assert queries(client, url) <= one

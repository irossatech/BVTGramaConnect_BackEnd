"""A block and "Discard all" are saved together or not at all (audit BE-DB-023).

A block writes the phone's fields, its CitizenBlock row and the audit entry; if one fails, none stays.
"Discard all" undoes the discards already made when one fails for an unexpected reason. Since 2026-11-21
a block changes no post (owner's request).
"""

import uuid
from unittest import mock

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.citizens import services
from apps.citizens.models import CitizenBlock, CitizenDevice
from apps.posts import services as post_services
from apps.posts.models import Category, Post, PostStatus
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


@pytest.fixture
def phone_posts(world):
    device = CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id, nonce="n-1",
    )
    ref = world.ref("kolakaluru")
    posts = [
        make_bare_post(
            citizen=device, posted_as_role="CITIZEN", posted_as_level=ref.level, posted_as_area_id=ref.id,
            category=Category.objects.first(), title=f"p{index}", body="b", target_level=ref.level,
            target_area_id=ref.id, status=PostStatus.PENDING, submitted_at=timezone.now(),
        )
        for index in range(3)
    ]
    return device, posts


def failing_on_second_discard():
    real = post_services.review
    calls = {"n": 0}

    def review(user, post, action, comment="", to_status=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the database went away")
        return real(user, post, action, comment, to_status)

    return mock.patch.object(post_services, "review", side_effect=review)


def pending_count():
    return Post.objects.filter(status=PostStatus.PENDING).count()


def test_a_failed_block_leaves_nothing(world, phone_posts):
    device, posts = phone_posts
    with mock.patch.object(services, "record", side_effect=RuntimeError("the database went away")), pytest.raises(RuntimeError):
        services.block(world.users["super"], posts[0], "spam", 7)
    device.refresh_from_db()
    assert device.blocked_at is None
    assert not CitizenBlock.objects.exists()
    assert pending_count() == 3


def test_a_failed_discard_all_undoes_the_discards_and_writes_no_summary(world, phone_posts):
    _device, posts = phone_posts
    with failing_on_second_discard(), pytest.raises(RuntimeError):
        services.discard_all(world.users["super"], posts[0], "spam")
    assert pending_count() == 3
    assert not AuditLog.objects.filter(action="citizen.posts_discarded").exists()


def test_a_block_leaves_every_post_as_it_was(world, phone_posts):
    device, posts = phone_posts
    Post.objects.filter(pk=posts[1].pk).update(status=PostStatus.PUBLISHED)
    services.block(world.users["super"], posts[0], "spam", 7)
    device.refresh_from_db()
    assert device.blocked_at is not None
    assert sorted(Post.objects.values_list("status", flat=True)) == sorted(
        [PostStatus.PENDING, PostStatus.PUBLISHED, PostStatus.PENDING]
    )

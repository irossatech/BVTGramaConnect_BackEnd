"""The data steps of 2026-11-21: citizens/0004 copies today's blocks into CitizenBlock; posts/0013 reopens
the posts a block closed (option A); posts/0014 reopens the posts a block discarded before 2026-11-20.
They run here with the current models, which have the same fields."""

import importlib
import uuid
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.citizens.models import CitizenBlock, CitizenDevice
from apps.posts import services as post_services
from apps.posts.models import Category, Post, PostStatus
from conftest import make_bare_post

pytestmark = pytest.mark.django_db

copy_blocks = importlib.import_module("apps.citizens.migrations.0004_backfill_citizen_blocks")
reopen = importlib.import_module("apps.posts.migrations.0013_reopen_blocked_posts")
reopen_old = importlib.import_module("apps.posts.migrations.0014_reopen_posts_discarded_by_blocks")


def device(world, android="", **fields):
    return CitizenDevice.objects.create(
        token_hash=uuid.uuid4().hex * 2, platform="android", village_id=world.ref("kolakaluru").id,
        nonce=uuid.uuid4().hex, android_id_hash=android, **fields,
    )


def pending_post(world, citizen, title="p"):
    ref = world.ref("kolakaluru")
    return make_bare_post(
        citizen=citizen, posted_as_role="CITIZEN", posted_as_level=ref.level, posted_as_area_id=ref.id,
        category=Category.objects.first(), title=title, body="b", target_level=ref.level, target_area_id=ref.id,
        status=PostStatus.PENDING, submitted_at=timezone.now() - timedelta(days=3),
    )


def test_copy_blocks_one_row_per_phone(world):
    now = timezone.now()
    sa = world.users["super"]
    running = dict(blocked_at=now, blocked_until=now + timedelta(days=5), blocked_by=sa, block_reason="spam")
    device(world, android="phone-a", **running)
    device(world, android="phone-a", **running)  # a reinstall: the same block on both rows
    device(world, blocked_at=now - timedelta(days=9), blocked_until=now - timedelta(days=2), block_reason="old")
    device(world)  # never blocked
    copy_blocks.copy_blocks(django_apps, None)
    assert CitizenBlock.objects.count() == 2
    live = CitizenBlock.objects.get(phone="phone-a")
    assert live.ended_at is None and live.reason == "spam" and live.blocked_by == sa
    done = CitizenBlock.objects.exclude(phone="phone-a").get()
    assert done.ended_at == done.blocked_until and done.ended_by is None
    copy_blocks.remove_copies(django_apps, None)
    assert not CitizenBlock.objects.exists()


def test_reopen_puts_blocked_posts_back_in_review(world):
    citizen = device(world)
    blocked = [pending_post(world, citizen, f"b{i}") for i in range(2)]
    for post in blocked:
        post_services.review(world.users["super"], post, "block", "spam")
    discarded = pending_post(world, citizen, "d")
    post_services.review(world.users["super"], discarded, "discard", "no")

    reopen.reopen(django_apps, None)
    for post in blocked:
        post.refresh_from_db()
        assert post.status == PostStatus.PENDING
        assert post.decided_at is None and post.decided_by is None and post.decided_by_role == ""
        assert post.submitted_at > timezone.now() - timedelta(minutes=1)
        last = post.review_actions.order_by("-created_at").first()
        assert (last.action, last.from_status, last.to_status, last.actor, last.comment) == (
            "REVERSE", "BLOCKED", "PENDING", None, reopen.COMMENT,
        )
    assert AuditLog.objects.filter(action="post.reversed", actor=None).count() == 2
    assert Post.objects.get(pk=discarded.pk).status == PostStatus.DISCARDED

    reopen.close_again(django_apps, None)
    assert {Post.objects.get(pk=post.pk).status for post in blocked} == {PostStatus.BLOCKED}
    assert not blocked[0].review_actions.filter(comment=reopen.COMMENT).exists()


def old_block(world, citizen, comment):
    """What a block did before 2026-11-20: the citizen.blocked audit entry, then the phone's discards."""
    sa = world.users["super"]
    record("citizen.blocked", actor=sa, actor_role="SUPER_ADMIN", target=citizen, data={"comment": comment, "days": 7})
    for post in Post.objects.filter(citizen__in=CitizenDevice.objects.filter(android_id_hash=citizen.android_id_hash),
                                    status=PostStatus.PENDING):
        post_services.review(sa, post, "discard", comment)


def test_posts_discarded_by_an_old_block_go_back_to_review(world):
    sa = world.users["super"]
    first, reinstall = device(world, android="phone-x"), device(world, android="phone-x")
    # "Discard all" before the block: it discarded first and audited after, so it never matches.
    by_discard_all = pending_post(world, first, "all")
    post_services.review(sa, by_discard_all, "discard", "spam")
    record("citizen.posts_discarded", actor=sa, actor_role="SUPER_ADMIN", target=first, data={"comment": "spam"})
    # A Village Admin's own discard stays too.
    by_reviewer = pending_post(world, first, "reviewer")
    post_services.review(world.users["va_kolakaluru"], by_reviewer, "discard", "వద్దు")
    closed = [pending_post(world, first, "a"), pending_post(world, reinstall, "b")]
    old_block(world, first, "spam")
    # Another phone's discard by the same Super Admin and comment is not this block's.
    other = pending_post(world, device(world, android="phone-y"), "other")
    post_services.review(sa, other, "discard", "spam")

    reopen_old.reopen(django_apps, None)
    for post in closed:
        post.refresh_from_db()
        assert post.status == PostStatus.PENDING
        assert post.decided_at is None and post.decided_by is None and post.decided_by_role == ""
        last = post.review_actions.order_by("-created_at").first()
        assert (last.action, last.from_status, last.to_status, last.actor, last.comment) == (
            "REVERSE", "DISCARDED", "PENDING", None, reopen_old.COMMENT,
        )
        assert AuditLog.objects.filter(action="post.reversed", target_id=str(post.pk), actor=None).exists()
    for post in (by_discard_all, by_reviewer, other):
        assert Post.objects.get(pk=post.pk).status == PostStatus.DISCARDED

    reopen_old.reopen(django_apps, None)  # running it again changes nothing
    assert Post.objects.filter(review_actions__comment=reopen_old.COMMENT).count() == 2

    reopen_old.discard_again(django_apps, None)
    for post in closed:
        post.refresh_from_db()
        assert post.status == PostStatus.DISCARDED and post.decided_by == sa and post.decided_at is not None
        assert not post.review_actions.filter(comment=reopen_old.COMMENT).exists()


def test_a_post_decided_again_after_the_old_block_is_left_alone(world):
    citizen = device(world, android="phone-z")
    post = pending_post(world, citizen, "later")
    old_block(world, citizen, "spam")
    post.refresh_from_db()
    post_services.review(world.users["super"], post, "reverse", "సరైనదే", to_status="PUBLISHED")
    post.refresh_from_db()
    # Discarded again later by a reverse: its latest action is no longer the block's discard.
    post_services.review(world.users["super"], post, "reverse", "మళ్ళీ", to_status="DISCARDED")
    reopen_old.reopen(django_apps, None)
    post.refresh_from_db()
    assert post.status == PostStatus.DISCARDED

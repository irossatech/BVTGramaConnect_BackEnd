"""The escalation job takes a bounded batch, oldest first, and alerts through jobs of their own (BE-DB-016).

Rules: a PENDING post older than REVIEW_ESCALATION_HOURS climbs one level per period; at most
ESCALATION_BATCH_SIZE posts per run, the oldest submission first; the alert is skipped for a post that was
decided before its alert job ran.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.notifications import receivers, tasks
from apps.notifications.models import Notification, NotificationKind
from apps.posts.models import Category, Post, PostStatus
from apps.posts.services import escalate_overdue
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


def overdue(world, hours, status=PostStatus.PENDING, area="kolakaluru", **extra):
    ref = world.ref(area)
    return make_bare_post(
        author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level=ref.level,
        posted_as_area_id=ref.id, category=Category.objects.get(key="announce"), title=f"{hours} h",
        body="b", target_level=ref.level, target_area_id=ref.id, status=status,
        submitted_at=timezone.now() - timedelta(hours=hours), **extra,
    )


def escalated_titles():
    return sorted(Post.objects.filter(escalated_at__isnull=False).values_list("title", flat=True))


def test_a_run_takes_at_most_the_batch_oldest_first(world, settings, django_capture_on_commit_callbacks):
    settings.ESCALATION_BATCH_SIZE = 2
    for hours in (30, 50, 40, 26, 60):
        overdue(world, hours)
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 2
    assert escalated_titles() == ["50 h", "60 h"]
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 2
    assert escalated_titles() == ["30 h", "40 h", "50 h", "60 h"]  # then 40 h and 30 h
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 1
    assert len(escalated_titles()) == 5
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 0  # each climbed once; the next level waits another period


def test_posts_with_no_level_left_never_fill_the_batch(world, settings, django_capture_on_commit_callbacks):
    # BE-DB-201: posts for Everyone, and posts already escalated to the state, have no level above them.
    # Older than every village post, they used to take the whole batch on every run, so nothing climbed.
    settings.ESCALATION_BATCH_SIZE = 2
    for hours in (100, 90, 80):
        overdue(world, hours, area="ap")  # for Everyone
    overdue(world, 95, escalation_level="STATE", escalated_at=timezone.now() - timedelta(hours=70))
    overdue(world, 30)
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 1
    assert Post.objects.get(title="30 h").escalation_level == "MANDAL"
    assert not Post.objects.filter(title__in=["100 h", "90 h", "80 h"], escalated_at__isnull=False).exists()
    assert Post.objects.get(title="95 h").escalation_level == "STATE"


def test_an_older_post_without_target_rows_climbs_from_its_first_area(world, django_capture_on_commit_callbacks):
    from apps.posts.models import PostTarget

    post = overdue(world, 30)
    PostTarget.objects.filter(post=post).delete()  # as posts written before targets existed
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 1
    post.refresh_from_db()
    assert post.escalation_level == "MANDAL"


def test_each_escalated_post_queues_its_own_alert_job(world, django_capture_on_commit_callbacks):
    posts = [overdue(world, hours) for hours in (30, 40)]
    with mock.patch.object(receivers, "async_task") as queued:
        with django_capture_on_commit_callbacks(execute=True):
            escalate_overdue()
    calls = {call.args[1]: call.args for call in queued.call_args_list}
    assert set(calls) == {str(post.pk) for post in posts}
    for args in calls.values():
        assert args[0] == "apps.notifications.tasks.alert_escalation" and args[2] == "MANDAL" and args[3] == 24


def test_the_alert_job_tells_the_mandal_admins(world):
    post = overdue(world, 30)
    assert tasks.alert_escalation(str(post.pk), "MANDAL", 24) >= 1
    told = set(Notification.objects.filter(kind=NotificationKind.ESCALATION).values_list("user__username", flat=True))
    assert "ma_tenali" in told


def test_the_alert_job_skips_a_post_decided_meanwhile(world):
    post = overdue(world, 30, status=PostStatus.PUBLISHED)
    assert tasks.alert_escalation(str(post.pk), "MANDAL", 24) == 0
    assert not Notification.objects.filter(kind=NotificationKind.ESCALATION).exists()

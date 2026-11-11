"""Notification fan-out reads each template and area once and writes the inbox in one insert (audit BE-DB-003).

Expected texts come from render(), the one-recipient path, in each recipient's language.
"""

from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.notifications import services
from apps.notifications.models import Notification, NotificationKind
from apps.posts.models import Category
from conftest import make_bare_post

pytestmark = pytest.mark.django_db


@pytest.fixture
def post(world):
    ref = world.ref("kolakaluru")
    return make_bare_post(
        author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level=ref.level,
        posted_as_area_id=ref.id, category=Category.objects.get(key="announce"), title="గ్రామ సభ",
        body="b", target_level=ref.level, target_area_id=ref.id, status="PENDING",
    )


def expected_title(post, language):
    return services.render("review_pending", language, **services._values(post, language))[:255]


def notify(post, users):
    with mock.patch.object(services, "async_task") as queued:
        with CaptureQueriesContext(connection) as ctx:
            created = services.notify_users(users, post=post, kind=NotificationKind.REVIEW_PENDING, template_key="review_pending")
    return created, queued, len(ctx.captured_queries)


def test_each_user_gets_an_inbox_item_and_a_push_in_their_language(world, post):
    User.objects.filter(username__in=["va_kolakaluru", "da_guntur"]).update(preferred_language="en")
    users = list(User.objects.filter(username__in=["va_kolakaluru", "ma_tenali", "da_guntur", "super"]))
    created, queued, _ = notify(post, users)
    assert len(created) == 4 and Notification.objects.filter(post=post).count() == 4
    for note in Notification.objects.filter(post=post).select_related("user"):
        assert note.title == expected_title(post, note.user.preferred_language)
        assert note.kind == NotificationKind.REVIEW_PENDING and note.body == post.title
    assert sorted(call.args[1] for call in queued.call_args_list) == sorted(str(user.pk) for user in users)
    assert {call.args[2] for call in queued.call_args_list} == {expected_title(post, "te"), expected_title(post, "en")}


def test_queries_do_not_grow_with_recipients(world, post):
    two = list(User.objects.filter(username__in=["va_kolakaluru", "ma_tenali"]))
    _, _, few = notify(post, two)
    everyone = list(User.objects.all())
    _, queued, many = notify(post, everyone)
    assert many <= few
    assert queued.call_count == len(everyone)


def test_nobody_to_tell_writes_nothing(world, post):
    created, queued, _ = notify(post, [])
    assert created == [] and queued.call_count == 0

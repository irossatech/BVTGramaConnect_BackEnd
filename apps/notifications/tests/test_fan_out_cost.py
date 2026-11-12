"""Telling reviewers about a post costs the same for 1 reviewer or 20 (audit BE_01, BE-PERF-001)."""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import RoleAssignment, User
from apps.accounts.roles import Role
from apps.notifications import services
from apps.notifications.models import Notification, NotificationKind
from apps.posts.models import Category
from apps.posts.services import PostInput, create_post


def make_post(world):
    return create_post(
        world.users["pm_kolakaluru"],
        PostInput(category=Category.objects.get(key="announce"), language="te", title="గ్రామ సభ", body="సభ",
                  target=world.ref("kolakaluru")),
        submit=False,
    )


def queries_to_notify(post, users) -> int:
    with CaptureQueriesContext(connection) as captured:
        services.notify_users(users, post=post, kind=NotificationKind.REVIEW_PENDING, template_key="review_pending")
    return len(captured)


def test_notifying_reviewers_does_not_cost_a_query_each(world, push_outbox, devices):
    post = make_post(world)
    for n in range(20):
        user = User.objects.create_user(username=f"extra_{n}", name=f"Extra {n}", password="x", preferred_language="te")
        RoleAssignment.objects.assign(user, Role.VILLAGE_ADMIN, world.ref("kolakaluru"))
    one = list(User.objects.filter(username="va_kolakaluru"))
    many = list(User.objects.filter(username__startswith="extra_")) + one
    # The cost grows with the number of languages (a text each), never with the number of people.
    assert queries_to_notify(post, many) == queries_to_notify(post, one)
    assert Notification.objects.filter(post=post).count() == 22  # va_kolakaluru twice, 20 extra


def test_each_reviewer_gets_the_text_in_their_language(world, push_outbox, devices):
    post = make_post(world)
    english = world.users["va_kolakaluru"]
    User.objects.filter(pk=english.pk).update(preferred_language="en")
    users = User.objects.filter(username__in=["va_kolakaluru", "ma_tenali"])
    services.notify_users(users, post=post, kind=NotificationKind.REVIEW_PENDING, template_key="review_pending")
    titles = {n.user.username: n.title for n in Notification.objects.filter(post=post).select_related("user")}
    assert titles["va_kolakaluru"] != titles["ma_tenali"]
    pushed = {message.token: message.title for message in push_outbox}
    assert pushed["tok-va_kolakaluru"] == titles["va_kolakaluru"] and pushed["tok-ma_tenali"] == titles["ma_tenali"]

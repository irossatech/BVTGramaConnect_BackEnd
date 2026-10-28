"""Who gets told what: topic pushes, reviewer alerts, author updates, escalation."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.geography.areas import AreaRef
from apps.notifications.models import Notification, NotificationKind
from apps.notifications.push import topic_for
from apps.posts.models import Category, Post
from apps.posts.services import PostInput, create_post, escalate_overdue, review

pytestmark = pytest.mark.django_db


def post_as(world, name, area_key, capture, *, language="te", title="గ్రామ సభ", body="రేపు ఉదయం 10 గంటలకు."):
    with capture(execute=True):
        return create_post(
            world.users[name],
            PostInput(
                category=Category.objects.get(key="announce"),
                language=language,
                title=title,
                body=body,
                target=world.ref(area_key),
            ),
        )


def decide(world, name, post, action, capture, comment=""):
    with capture(execute=True):
        return review(world.users[name], Post.objects.get(pk=post.pk), action, comment)


def inbox(user, kind=None):
    rows = Notification.objects.filter(user=user)
    return rows.filter(kind=kind) if kind else rows


def test_topic_names():
    assert topic_for("VILLAGE", 12) == "village_12"
    assert topic_for("MANDAL", 3) == "mandal_3"
    assert topic_for("DISTRICT", 1) == "district_1"
    assert topic_for("STATE", 1) == "state_1"


def test_panchayat_post_alerts_exactly_the_reviewers_in_area(world, devices, push_outbox, django_capture_on_commit_callbacks):
    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    assert post.status == "PENDING"
    alerted = {n.user.name for n in Notification.objects.filter(kind=NotificationKind.REVIEW_PENDING)}
    assert alerted == {"va_kolakaluru", "ma_tenali", "da_guntur"}
    assert {m.token for m in push_outbox} == {"tok-va_kolakaluru", "tok-ma_tenali", "tok-da_guntur"}
    assert all(m.topic is None for m in push_outbox)
    note = inbox(world.users["va_kolakaluru"]).get()
    assert note.title == "కొలకలూరులో కొత్త పోస్ట్ సమీక్ష కోసం వేచి ఉంది: గ్రామ సభ"
    assert note.post_id == post.id


def test_reviewer_alert_in_the_reviewers_language(world, devices, django_capture_on_commit_callbacks):
    user = world.users["ma_tenali"]
    user.preferred_language = "en"
    user.save()
    post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    assert inbox(user).get().title == "New post waiting for review in Kolakaluru: గ్రామ సభ"


def test_publishing_pushes_to_the_village_topic_and_tells_the_author(world, devices, push_outbox, django_capture_on_commit_callbacks):
    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    push_outbox.clear()
    decide(world, "va_kolakaluru", post, "publish", django_capture_on_commit_callbacks)

    topic_pushes = [m for m in push_outbox if m.topic]
    assert [m.topic for m in topic_pushes] == [f"village_{world.areas['kolakaluru'].id}"]
    assert topic_pushes[0].title == "కొలకలూరు: కొత్త గ్రామ చాటింపులు"
    assert topic_pushes[0].body == "గ్రామ సభ"
    assert topic_pushes[0].data == {
        "type": "POST_PUBLISHED", "post_id": str(post.id), "area_level": "VILLAGE",
        "area_id": str(world.areas["kolakaluru"].id), "urgent": "0",
    }
    assert topic_pushes[0].priority == "normal"  # ordinary post; urgent ones go out "high"
    author_note = inbox(world.users["pm_kolakaluru"], NotificationKind.POST_PUBLISHED).get()
    assert "ప్రచురించబడింది" in author_note.title
    assert "tok-pm_kolakaluru" in {m.token for m in push_outbox}


@pytest.mark.parametrize(
    "action, kind, text",
    [
        ("recheck", NotificationKind.POST_RECHECK, "మీ పోస్ట్‌ను సరిచేయాలి: ఫోటో జోడించండి"),
        ("discard", NotificationKind.POST_DISCARDED, "మీ పోస్ట్ తిరస్కరించబడింది. కారణం: ఫోటో జోడించండి"),
    ],
)
def test_author_hears_about_recheck_and_discard_with_the_comment(world, devices, django_capture_on_commit_callbacks, action, kind, text):
    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    decide(world, "ma_tenali", post, action, django_capture_on_commit_callbacks, comment="ఫోటో జోడించండి")
    assert inbox(world.users["pm_kolakaluru"], kind).get().title == text


def test_take_down_tells_the_author(world, devices, django_capture_on_commit_callbacks):
    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    decide(world, "va_kolakaluru", post, "publish", django_capture_on_commit_callbacks)
    decide(world, "da_guntur", post, "unpublish", django_capture_on_commit_callbacks, comment="తప్పు సమాచారం")
    note = inbox(world.users["pm_kolakaluru"], NotificationKind.POST_UNPUBLISHED).get()
    assert "తప్పు సమాచారం" in note.title


@pytest.mark.parametrize(
    "author, area_key, topic_level",
    [("ma_tenali", "tenali", "mandal"), ("da_guntur", "guntur", "district"), ("da_guntur", "kaza", "village")],
)
def test_admin_post_publishes_at_once_to_its_target_topic(world, devices, push_outbox, django_capture_on_commit_callbacks, author, area_key, topic_level):
    post = post_as(world, author, area_key, django_capture_on_commit_callbacks)
    assert post.status == "PUBLISHED"
    assert [m.topic for m in push_outbox if m.topic] == [f"{topic_level}_{world.areas[area_key].id}"]
    assert not Notification.objects.filter(kind=NotificationKind.REVIEW_PENDING).exists()
    assert not inbox(world.users[author]).exists()


def test_topic_push_uses_the_posts_language(world, push_outbox, django_capture_on_commit_callbacks):
    post_as(world, "ma_tenali", "tenali", django_capture_on_commit_callbacks, language="en", title="Gram Sabha",
            body="Tomorrow at 10 AM.")
    [message] = [m for m in push_outbox if m.topic]
    assert message.title == "Tenali: new Announcements"


def test_escalation_alerts_the_next_level_up(world, devices, django_capture_on_commit_callbacks):
    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    Notification.objects.all().delete()
    Post.objects.filter(pk=post.pk).update(submitted_at=timezone.now() - timedelta(hours=25))
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 1
    alerted = {n.user.name for n in Notification.objects.filter(kind=NotificationKind.ESCALATION)}
    assert alerted == {"ma_tenali"}
    assert "24 గంటలుగా" in Notification.objects.get(kind=NotificationKind.ESCALATION).title


def test_escalation_to_state_alerts_super_admins(world, devices, django_capture_on_commit_callbacks):
    from apps.notifications.services import alert_escalation

    post = post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    Notification.objects.all().delete()
    alert_escalation(post, "STATE", 72)
    assert {n.user.name for n in Notification.objects.all()} == {"super"}


def test_blocked_reviewers_are_not_alerted(world, devices, django_capture_on_commit_callbacks):
    from apps.accounts.models import User

    User.objects.filter(pk=world.users["da_guntur"].pk).update(is_active=False)
    post_as(world, "pm_kolakaluru", "kolakaluru", django_capture_on_commit_callbacks)
    alerted = {n.user.name for n in Notification.objects.filter(kind=NotificationKind.REVIEW_PENDING)}
    assert alerted == {"va_kolakaluru", "ma_tenali"}


def test_a_failing_receiver_never_breaks_the_post_change(world, monkeypatch, django_capture_on_commit_callbacks):
    from apps.notifications import services

    def boom(*args, **kwargs):
        raise RuntimeError("push service down")

    monkeypatch.setattr(services, "announce_to_citizens", boom)
    post = post_as(world, "ma_tenali", "tenali", django_capture_on_commit_callbacks)
    assert Post.objects.get(pk=post.pk).status == "PUBLISHED"


def test_reviewers_for_a_mandal_post_skip_village_admins(world):
    from apps.notifications.services import admins_over

    names = {u.name for u in admins_over(AreaRef("MANDAL", world.areas["tenali"].id))}
    assert names == {"ma_tenali", "da_guntur"}


def test_urgent_posts_push_at_high_priority(world, devices, push_outbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        post = create_post(
            world.users["ma_tenali"],
            PostInput(
                category=Category.objects.get(key="announce"), language="te", title="వరద హెచ్చరిక",
                body="నది ఒడ్డుకు వెళ్లవద్దు.", target=world.ref("tenali"), is_urgent=True,
            ),
        )
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == "high"
    assert message.data["urgent"] == "1" and message.data["post_id"] == str(post.id)


def test_fcm_payload_carries_the_priority():
    from apps.notifications.push import PushMessage

    with pytest.raises(ValueError):
        PushMessage(title="t", body="b", data={}, topic="village_1", priority="urgent")
    assert PushMessage(title="t", body="b", data={}, topic="village_1").priority == "high"


@pytest.mark.parametrize("priority, android, apns", [("high", "high", "10"), ("normal", "normal", "5")])
def test_fcm_request_sets_android_and_apns_priority(priority, android, apns):
    import json

    from apps.notifications.push import FcmGateway, PushMessage

    sent = {}

    class Session:
        def post(self, url, data, headers, timeout):
            sent["payload"] = json.loads(data.decode("utf-8"))
            return type("Response", (), {"status_code": 200, "text": ""})()

    gateway = FcmGateway.__new__(FcmGateway)
    gateway.enabled, gateway._session, gateway.project_id = True, Session(), "demo"
    gateway.send(PushMessage(title="t", body="b", data={"urgent": "1"}, topic="village_1", priority=priority))
    message = sent["payload"]["message"]
    assert message["android"] == {"priority": android}
    assert message["apns"] == {"headers": {"apns-priority": apns}}

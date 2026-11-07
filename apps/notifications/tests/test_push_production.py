"""Push in production (mobile team's plan, 28 Oct 2026): the qa topic prefix, the Android channel,
tag and expiry, the iPhone sound, and the high-priority switch."""

import json

import pytest

from apps.notifications.push import FcmGateway, PushMessage, build_payload, topic_for
from apps.posts.models import Category
from apps.posts.services import PostInput, create_post


def payload(**kwargs):
    kwargs.setdefault("title", "t")
    kwargs.setdefault("body", "b")
    if "token" not in kwargs:
        kwargs.setdefault("topic", "village_1")
    return build_payload(PushMessage(**kwargs))["message"]


# --- 1b. Topic prefix -----------------------------------------------------------------------------


def test_topics_have_no_prefix_by_default():
    assert topic_for("VILLAGE", 12) == "village_12"


def test_qa_prefix_goes_in_front_of_every_topic(settings):
    settings.FCM_TOPIC_PREFIX = "qa_"
    assert [topic_for(level, 7) for level in ("VILLAGE", "MANDAL", "DISTRICT", "STATE")] == [
        "qa_village_7", "qa_mandal_7", "qa_district_7", "qa_state_7",
    ]


@pytest.mark.django_db
def test_a_published_post_on_qa_goes_only_to_qa_topics(settings, world, push_outbox, django_capture_on_commit_callbacks):
    settings.FCM_TOPIC_PREFIX = "qa_"
    with django_capture_on_commit_callbacks(execute=True):
        create_post(world.users["ma_tenali"], PostInput(
            category=Category.objects.get(key="announce"), language="te", title="గ్రామ సభ",
            body="రేపు ఉదయం.", target=world.ref("tenali"),
        ))
    assert [m.topic for m in push_outbox if m.topic] == [f"qa_mandal_{world.areas['tenali'].id}"]


@pytest.mark.django_db
def test_pushes_to_one_phone_carry_no_prefix(settings, world, devices, push_outbox, django_capture_on_commit_callbacks):
    settings.FCM_TOPIC_PREFIX = "qa_"
    with django_capture_on_commit_callbacks(execute=True):
        create_post(world.users["pm_kolakaluru"], PostInput(
            category=Category.objects.get(key="announce"), language="te", title="గ్రామ సభ",
            body="రేపు ఉదయం.", target=world.ref("kolakaluru"),
        ))
    assert {m.token for m in push_outbox} >= {"tok-va_kolakaluru"}
    assert all(m.topic is None for m in push_outbox)


@pytest.mark.parametrize("prefix", ["qa_", "staging-", ""])
def test_settings_accept_a_valid_prefix(prefix, monkeypatch):
    import importlib

    import grama.settings as base

    monkeypatch.setenv("FCM_TOPIC_PREFIX", prefix)
    try:
        assert importlib.reload(base).FCM_TOPIC_PREFIX == prefix
    finally:
        monkeypatch.delenv("FCM_TOPIC_PREFIX")
        importlib.reload(base)


def test_settings_refuse_a_prefix_fcm_would_reject(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import grama.settings as base

    monkeypatch.setenv("FCM_TOPIC_PREFIX", "qa test/")
    try:
        with pytest.raises(ImproperlyConfigured):
            importlib.reload(base)
    finally:
        monkeypatch.delenv("FCM_TOPIC_PREFIX")
        importlib.reload(base)


# --- 3. Payload -------------------------------------------------------------------------------------


def test_ordinary_post_goes_on_the_news_channel_with_its_tag_and_a_day_to_live():
    message = payload(data={"type": "POST_PUBLISHED", "post_id": "41", "urgent": "0"}, priority="normal")
    assert message["android"] == {
        "priority": "normal", "ttl": "86400s", "notification": {"channel_id": "news", "tag": "41"},
    }


def test_urgent_post_goes_on_the_urgent_channel():
    message = payload(data={"type": "POST_PUBLISHED", "post_id": "41", "urgent": "1"})
    assert message["android"]["notification"]["channel_id"] == "urgent"


def test_escalation_goes_on_the_urgent_channel():
    message = payload(data={"type": "ESCALATION", "post_id": "41", "urgent": "0"}, token="tok")
    assert message["android"]["notification"]["channel_id"] == "urgent"


@pytest.mark.parametrize("kind", ["REVIEW_PENDING", "POST_RECHECK", "SERVICE_REQUEST"])
def test_other_kinds_go_on_the_news_channel(kind):
    assert payload(data={"type": kind}, token="tok")["android"]["notification"]["channel_id"] == "news"


def test_service_request_is_tagged_by_its_request():
    message = payload(data={"type": "SERVICE_REQUEST_UPDATED", "request_id": "9"}, token="tok")
    assert message["android"]["notification"]["tag"] == "9"
    assert message["token"] == "tok" and "topic" not in message


def test_no_tag_without_a_post_or_request():
    note = payload(data={"type": "POST_PUBLISHED"})["android"]["notification"]
    assert note == {"channel_id": "news"}


def test_no_field_is_null():
    def walk(value):
        if isinstance(value, dict):
            return all(v is not None and walk(v) for v in value.values())
        return True

    assert walk(payload(data={"type": "POST_PUBLISHED", "post_id": None}))


def test_iphones_get_the_default_sound():
    for priority, header in (("high", "10"), ("normal", "5")):
        assert payload(data={}, priority=priority)["apns"] == {
            "headers": {"apns-priority": header}, "payload": {"aps": {"sound": "default"}},
        }


def test_ttl_follows_the_setting(settings):
    settings.PUSH_TTL_SECONDS = 3600
    assert payload(data={})["android"]["ttl"] == "3600s"


def test_telugu_text_reaches_fcm_as_utf8():
    sent = {}

    class Session:
        def post(self, url, data, headers, timeout):
            sent["payload"] = json.loads(data.decode("utf-8"))
            return type("Response", (), {"status_code": 200, "text": ""})()

    gateway = FcmGateway.__new__(FcmGateway)
    gateway.enabled, gateway._session, gateway.project_id = True, Session(), "demo"
    message = PushMessage(title="కొలకలూరు: కొత్త గ్రామ చాటింపులు", body="గ్రామ సభ",
                          data={"type": "POST_PUBLISHED", "post_id": "5", "urgent": "0"}, topic="qa_village_1")
    gateway.send(message)
    assert sent["payload"] == build_payload(message)
    assert sent["payload"]["message"]["notification"]["title"] == "కొలకలూరు: కొత్త గ్రామ చాటింపులు"


# --- 2. High priority for every post (approved 2026-11-04; on by default) ----------------------------


def publish(world, capture, *, urgent=False):
    with capture(execute=True):
        create_post(world.users["ma_tenali"], PostInput(
            category=Category.objects.get(key="announce"), language="te", title="గ్రామ సభ",
            body="రేపు ఉదయం.", target=world.ref("tenali"), is_urgent=urgent,
        ))


def test_every_post_is_high_priority_by_default():
    from django.conf import settings

    assert settings.PUSH_ALL_POSTS_HIGH_PRIORITY is True


@pytest.mark.django_db
def test_ordinary_post_goes_out_high_on_the_news_channel(world, push_outbox, django_capture_on_commit_callbacks):
    publish(world, django_capture_on_commit_callbacks)
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == "high"
    assert build_payload(message)["message"]["android"]["notification"]["channel_id"] == "news"


@pytest.mark.django_db
def test_urgent_post_goes_out_high_on_the_urgent_channel(world, push_outbox, django_capture_on_commit_callbacks):
    publish(world, django_capture_on_commit_callbacks, urgent=True)
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == "high"
    assert build_payload(message)["message"]["android"]["notification"]["channel_id"] == "urgent"


@pytest.mark.django_db
@pytest.mark.parametrize("urgent, priority", [(False, "normal"), (True, "high")])
def test_switched_off_only_urgent_posts_go_out_high(
    settings, world, push_outbox, django_capture_on_commit_callbacks, urgent, priority
):
    settings.PUSH_ALL_POSTS_HIGH_PRIORITY = False
    publish(world, django_capture_on_commit_callbacks, urgent=urgent)
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == priority

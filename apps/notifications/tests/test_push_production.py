"""Push in production (mobile team's plan, 28 Oct 2026): the qa topic prefix, the Android channel,
tag and expiry, the iPhone sound, the high-priority switch and the /devices/ rate limit."""

import json

import pytest
from rest_framework.test import APIClient

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

    import grama.settings.base as base

    monkeypatch.setenv("FCM_TOPIC_PREFIX", prefix)
    try:
        assert importlib.reload(base).FCM_TOPIC_PREFIX == prefix
    finally:
        monkeypatch.delenv("FCM_TOPIC_PREFIX")
        importlib.reload(base)


def test_settings_refuse_a_prefix_fcm_would_reject(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import grama.settings.base as base

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


# --- 2. High priority for every post (switch, off by default: owner decision Q19) --------------------


def publish_ordinary(world, capture):
    with capture(execute=True):
        create_post(world.users["ma_tenali"], PostInput(
            category=Category.objects.get(key="announce"), language="te", title="గ్రామ సభ",
            body="రేపు ఉదయం.", target=world.ref("tenali"),
        ))


@pytest.mark.django_db
def test_ordinary_posts_stay_normal_priority_by_default(world, push_outbox, django_capture_on_commit_callbacks):
    publish_ordinary(world, django_capture_on_commit_callbacks)
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == "normal"


@pytest.mark.django_db
def test_switch_sends_every_post_at_high_priority_and_keeps_the_news_channel(
    settings, world, push_outbox, django_capture_on_commit_callbacks
):
    settings.PUSH_ALL_POSTS_HIGH_PRIORITY = True
    publish_ordinary(world, django_capture_on_commit_callbacks)
    [message] = [m for m in push_outbox if m.topic]
    assert message.priority == "high"
    assert build_payload(message)["message"]["android"]["notification"]["channel_id"] == "news"


# --- 4. Rate limit on /devices/ -----------------------------------------------------------------------


def register(client, token):
    return client.post("/api/v1/devices/", {"fcm_token": token, "platform": "android", "language": "te"}, format="json")


@pytest.mark.django_db
def test_devices_are_limited_per_ip(settings):
    settings.DEVICE_THROTTLE_RATE = "3/hour"
    client = APIClient()
    assert [register(client, f"tok-{i}").status_code for i in range(3)] == [201, 201, 201]
    response = register(client, "tok-4")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert response.json()["error"]["retry_after_s"] > 0
    assert response.has_header("Retry-After")


@pytest.mark.django_db
def test_patch_and_delete_count_against_the_same_limit(settings):
    settings.DEVICE_THROTTLE_RATE = "3/hour"
    client = APIClient()
    device_id = register(client, "tok-1").json()["id"]
    assert client.patch(f"/api/v1/devices/{device_id}/", {"language": "en"}, format="json").status_code == 200
    assert client.delete(f"/api/v1/devices/{device_id}/").status_code == 204
    assert register(client, "tok-2").status_code == 429


@pytest.mark.django_db
def test_another_ip_has_its_own_count(settings):
    settings.DEVICE_THROTTLE_RATE = "1/hour"
    assert register(APIClient(REMOTE_ADDR="10.0.0.1"), "tok-1").status_code == 201
    assert register(APIClient(REMOTE_ADDR="10.0.0.1"), "tok-2").status_code == 429
    assert register(APIClient(REMOTE_ADDR="10.0.0.2"), "tok-3").status_code == 201


@pytest.mark.django_db
def test_officials_are_limited_too(settings, api, world):
    settings.DEVICE_THROTTLE_RATE = "1/hour"
    official = api(world.users["ma_tenali"])
    assert register(official, "tok-1").status_code == 201
    assert register(official, "tok-1").status_code == 429


def test_default_limit_leaves_room_for_a_shared_mobile_network():
    from django.conf import settings

    count, period = settings.DEVICE_THROTTLE_RATE.split("/")
    assert period == "hour" and int(count) >= 300

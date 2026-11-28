"""Retries with backoff, audit on final failure, dead tokens, FCM switched off."""

import logging

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from grama.retry import PermanentDeliveryError, backoff_delays, with_retries
from apps.notifications import push
from apps.notifications.models import Device
from apps.notifications.push import InvalidDeviceToken, LocmemPushGateway
from apps.notifications.tasks import send_push_to_topic, send_push_to_user

pytestmark = pytest.mark.django_db


def test_backoff_waits_one_then_four_seconds():
    assert backoff_delays(3, 1.0) == [1.0, 4.0]
    assert backoff_delays(4, 1.0) == [1.0, 4.0, 16.0]
    waits = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("timeout")
        return "ok"

    assert with_retries(flaky, attempts=3, base_delay=1.0, sleep=waits.append) == "ok"
    assert waits == [1.0, 4.0]


def test_permanent_errors_are_not_retried():
    calls = {"n": 0}

    def dead():
        calls["n"] += 1
        raise PermanentDeliveryError("gone")

    with pytest.raises(PermanentDeliveryError):
        with_retries(dead, attempts=3, base_delay=0)
    assert calls["n"] == 1


# --- Push -------------------------------------------------------------------------------


def test_topic_push_recovers_after_a_transient_failure(db):
    LocmemPushGateway.failures.append(RuntimeError("503"))
    assert send_push_to_topic("village_1", "t", "b", {}) is True
    assert len(LocmemPushGateway.outbox) == 1
    assert not AuditLog.objects.filter(action="push.failed").exists()


def test_topic_push_gives_up_after_three_tries_and_audits(db):
    LocmemPushGateway.failures.extend([RuntimeError("503")] * 3)
    assert send_push_to_topic("village_1", "t", "b", {}) is False
    entry = AuditLog.objects.get(action="push.failed")
    assert entry.type == "system" and entry.via == "system"
    assert entry.data == {"target": "topic village_1", "attempts": 3, "error": "RuntimeError"}


def test_dead_device_token_is_forgotten_without_retry(world):
    device = Device.objects.create(fcm_token="dead", platform="android", user=world.users["ma_tenali"])
    LocmemPushGateway.failures.append(InvalidDeviceToken("UNREGISTERED"))
    assert send_push_to_user(str(world.users["ma_tenali"].pk), "t", "b", {}) == 0
    assert not Device.objects.filter(pk=device.pk).exists()
    assert not AuditLog.objects.filter(action="push.failed").exists()


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_fcm_without_credentials_is_disabled_with_one_log_line(db, settings, monkeypatch):
    settings.PUSH_GATEWAY = "apps.notifications.push.FcmGateway"
    settings.FCM_CREDENTIALS_FILE = ""
    monkeypatch.setattr(push, "_disabled_logged", False)
    handler = _ListHandler()
    logger = logging.getLogger("grama.push")
    logger.addHandler(handler)
    try:
        assert send_push_to_topic("village_1", "t", "b", {}) is False
        assert send_push_to_topic("village_2", "t", "b", {}) is False
    finally:
        logger.removeHandler(handler)
    lines = [message for message in handler.messages if "FCM disabled" in message]
    assert len(lines) == 1 and "no credentials" in lines[0]
    assert not AuditLog.objects.exists()

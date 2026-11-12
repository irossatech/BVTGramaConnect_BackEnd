"""A push that must wait before its next try is scheduled, never slept on in the worker
(audit BE_01, BE-PERF-005)."""

import ast
import time

import pytest
from django_q.models import Schedule

from apps.audit.models import AuditLog
from apps.notifications import tasks
from apps.notifications.models import Device
from apps.notifications.push import LocmemPushGateway

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def real_waits(settings, monkeypatch):
    settings.DELIVERY_RETRY_BASE_SECONDS = 1.0  # as in production: 1 s, then 4 s
    settings.DELIVERY_RETRY_ATTEMPTS = 3

    def no_sleeping(seconds):
        raise AssertionError(f"slept {seconds} s in the worker")

    monkeypatch.setattr(time, "sleep", no_sleeping)


def run(schedule: Schedule):
    """What the django-q scheduler does when the one-off job is due."""
    module, name = schedule.func.rsplit(".", 1)
    assert module == "apps.notifications.tasks"
    return getattr(tasks, name)(*ast.literal_eval(schedule.args))


def test_a_failed_topic_push_is_scheduled_and_goes_out_on_the_retry():
    LocmemPushGateway.failures.append(RuntimeError("503"))
    assert tasks.send_push_to_topic("village_1", "t", "b", {"post_id": "p"}) is False
    retry = Schedule.objects.get()
    assert retry.schedule_type == Schedule.ONCE and retry.func == tasks.TOPIC_TASK
    assert ast.literal_eval(retry.args) == ("village_1", "t", "b", {"post_id": "p"}, "high", 2)
    assert run(retry) is True
    assert [message.topic for message in LocmemPushGateway.outbox] == ["village_1"]


def test_after_the_last_try_the_failure_is_audited(world):
    device = Device.objects.create(fcm_token="tok", platform="android", user=world.users["ma_tenali"])
    LocmemPushGateway.failures.extend([RuntimeError("503")] * 3)
    assert tasks.send_push_to_users([[str(world.users["ma_tenali"].pk), "t"]], "b", {}) == 0
    second = Schedule.objects.get()
    assert second.func == tasks.DEVICE_TASK and ast.literal_eval(second.args)[-1] == 2
    assert run(second) is False
    third = Schedule.objects.exclude(pk=second.pk).get()
    assert ast.literal_eval(third.args)[-1] == 3
    assert run(third) is False
    entry = AuditLog.objects.get(action="push.failed")
    assert entry.data == {"target": f"device {device.pk}", "attempts": 3, "error": "RuntimeError"}
    assert Schedule.objects.count() == 2  # no fourth try

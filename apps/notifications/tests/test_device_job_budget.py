"""A push job for many officials' devices hands the devices it has not reached to a new job before the
worker's time limit, so it is never stopped and run again from the start, and no device gets the same
push twice (finding BE-JOB-201, as test_topic_job_budget does for topics).

A job that raised is not run again by the queue either: Q_CLUSTER "ack_failures" (BE-JOB-201).
"""

import itertools
import time

import pytest
from django.conf import settings as django_settings

from apps.notifications import tasks
from apps.notifications.models import Device

pytestmark = pytest.mark.django_db

NAMES = ["super", "da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"]


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda func, *args, **kwargs: jobs.append((func, args, kwargs)))
    return jobs


@pytest.fixture
def phones(world):
    """One phone per official in NAMES; the job sends in device-id order."""
    for name in NAMES:
        Device.objects.create(fcm_token=f"tok-{name}", platform="android", user=world.users[name])
    return [d.fcm_token for d in Device.objects.order_by("pk")]


def messages(world):
    return [[str(world.users[name].pk), f"Title for {name}"] for name in NAMES]


def clock(monkeypatch, *seconds):
    """time.monotonic() answers these values in turn, then keeps the last one."""
    values = itertools.chain(seconds, itertools.repeat(seconds[-1]))
    monkeypatch.setattr(time, "monotonic", lambda: next(values))


def test_a_failed_job_is_not_run_again_by_the_queue():
    from django_q.conf import Conf

    assert django_settings.Q_CLUSTER["ack_failures"] is True
    assert Conf.ACK_FAILURES is True


def test_every_device_in_one_job_while_there_is_time(world, phones, push_outbox, queued, settings):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    assert tasks.send_push_to_users(messages(world), "body", {"post_id": "7"}) == 5
    assert [m.token for m in push_outbox] == phones
    assert queued == []


def test_out_of_time_the_rest_goes_to_a_new_job_and_no_device_gets_it_twice(world, phones, push_outbox, queued, settings, monkeypatch):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    # start at 0 (deadline 60); checks before devices 2 and 3 see 10 and 30, before device 4: 61.
    clock(monkeypatch, 0, 10, 30, 61)
    assert tasks.send_push_to_users(messages(world), "body", {"post_id": "7"}) == 3
    assert [m.token for m in push_outbox] == phones[:3]

    [(func, args, kwargs)] = queued
    assert func == tasks.USERS_TASK
    rest = [str(d.pk) for d in Device.objects.order_by("pk")[3:]]
    assert args == (messages(world), "body", {"post_id": "7"}, rest)
    assert kwargs == {"group": "push"}

    # The new job sends exactly the rest, each with its own official's title.
    monkeypatch.setattr(time, "monotonic", lambda: 0)
    assert tasks.send_push_to_users(*args) == 2
    assert sorted(m.token for m in push_outbox) == sorted(phones)
    titles = {m.token: m.title for m in push_outbox}
    assert titles == {f"tok-{name}": f"Title for {name}" for name in NAMES}


def test_the_first_device_is_always_sent_so_the_job_moves_on(world, phones, push_outbox, queued, settings, monkeypatch):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    clock(monkeypatch, 0, 100)  # already late when the first device is done
    assert tasks.send_push_to_users(messages(world), "body", {}) == 1
    assert [m.token for m in push_outbox] == phones[:1]
    assert queued[0][1][3] == [str(d.pk) for d in Device.objects.order_by("pk")[1:]]


def test_a_continuing_job_never_reaches_a_device_outside_its_officials(world, phones, push_outbox, queued):
    """The device ids of a continuing job only narrow the officials' devices, never widen them."""
    stranger = Device.objects.create(fcm_token="tok-stranger", platform="android", user=world.users["va_kaza"])
    assert tasks.send_push_to_users(messages(world)[:1], "body", {}, [str(stranger.pk)]) == 0
    assert push_outbox == []

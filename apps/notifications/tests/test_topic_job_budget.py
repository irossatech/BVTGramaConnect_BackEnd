"""A post's topic job hands the topics it has not reached to a new job before the worker's time
limit, so it is never stopped and re-run, and no topic gets the same push twice (BE_02, BE-REL-101).
"""

import itertools
import time

import pytest

from apps.notifications import tasks

TOPICS = [f"village_{n}" for n in range(1, 6)]


@pytest.fixture
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda func, *args, **kwargs: jobs.append((func, args, kwargs)))
    return jobs


def clock(monkeypatch, *seconds):
    """time.monotonic() answers these values in turn, then keeps the last one."""
    values = itertools.chain(seconds, itertools.repeat(seconds[-1]))
    monkeypatch.setattr(time, "monotonic", lambda: next(values))


def test_all_topics_in_one_job_while_there_is_time(push_outbox, queued, settings):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    assert tasks.send_push_to_topics(TOPICS, "t", "b", {}, "high") == 5
    assert [m.topic for m in push_outbox] == TOPICS
    assert queued == []


def test_out_of_time_the_rest_goes_to_a_new_job_and_nothing_is_sent_twice(push_outbox, queued, settings, monkeypatch):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    # start at 0 (deadline 60); checks before topics 2 and 3 see 10 and 30, before topic 4: 61.
    clock(monkeypatch, 0, 10, 30, 61)
    assert tasks.send_push_to_topics(TOPICS, "title", "body", {"post_id": "7"}, "normal") == 3
    assert [m.topic for m in push_outbox] == TOPICS[:3]

    [(func, args, kwargs)] = queued
    assert func == "apps.notifications.tasks.send_push_to_topics"
    assert args == (TOPICS[3:], "title", "body", {"post_id": "7"}, "normal")
    assert kwargs == {"group": "push"}

    # The new job sends exactly the rest.
    monkeypatch.setattr(time, "monotonic", lambda: 0)
    assert tasks.send_push_to_topics(*args) == 2
    assert sorted(m.topic for m in push_outbox) == sorted(TOPICS)


def test_the_first_topic_is_always_sent_so_the_job_moves_on(push_outbox, queued, settings, monkeypatch):
    settings.PUSH_TOPICS_JOB_SECONDS = 60
    clock(monkeypatch, 0, 100)  # already late when the first topic is done
    assert tasks.send_push_to_topics(TOPICS, "t", "b", {}, "high") == 1
    assert [m.topic for m in push_outbox] == TOPICS[:1]
    assert queued[0][1][0] == TOPICS[1:]

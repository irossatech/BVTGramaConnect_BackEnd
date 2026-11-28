"""The health check says whether the background worker is running (BE_02, BE-REL-103).

A stopped qcluster means no pushes, no escalation and no translation, while the site looks fine.
The plain address stays 200 either way, since deploys call health/ before they start the worker;
?strict=1, the uptime monitor's address, answers 503 then (BE-OBS-201).
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from django_q.models import Schedule

from apps.posts.tasks import ESCALATION_SCHEDULE

pytestmark = pytest.mark.django_db


def schedule(next_run):
    Schedule.objects.create(name=ESCALATION_SCHEDULE, func="apps.posts.tasks.run_escalation",
                            schedule_type=Schedule.MINUTES, minutes=15, next_run=next_run)


def health(client):
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    return response.json()["worker"]


def test_not_scheduled_before_ensure_schedules(client):
    assert health(client) == "not_scheduled"


def test_ok_while_the_next_run_is_ahead_or_just_due(client, settings):
    settings.ESCALATION_CHECK_MINUTES = 15
    schedule(timezone.now() + timedelta(minutes=10))
    assert health(client) == "ok"
    Schedule.objects.update(next_run=timezone.now() - timedelta(minutes=5))  # due, the worker will pick it up
    assert health(client) == "ok"


def test_late_when_runs_were_missed(client, settings):
    settings.ESCALATION_CHECK_MINUTES = 15
    schedule(timezone.now() - timedelta(minutes=40))
    assert health(client) == "late"


# --- Uptime monitor and failed jobs (BE-OBS-201, BE-OBS-204, 2026-11-27) ------------------------------

def strict(client):
    return client.get("/api/v1/health/", {"strict": "1"})


def test_strict_answers_503_while_the_worker_is_not_running(client, settings):
    settings.ESCALATION_CHECK_MINUTES = 15
    assert strict(client).status_code == 503 and strict(client).json()["status"] == "worker_down"  # not scheduled
    schedule(timezone.now() - timedelta(minutes=40))
    response = strict(client)
    assert response.status_code == 503
    assert response.json()["worker"] == "late" and response.json()["status"] == "worker_down"
    assert client.get("/api/v1/health/").status_code == 200  # the deploy scripts' address stays 200


def test_strict_answers_200_while_the_worker_runs(client, settings):
    settings.ESCALATION_CHECK_MINUTES = 15
    schedule(timezone.now() + timedelta(minutes=10))
    response = strict(client)
    assert response.status_code == 200 and response.json()["status"] == "ok" and response.json()["worker"] == "ok"


def test_a_database_that_does_not_answer_is_a_plain_503(client):
    from unittest import mock

    from django.db import OperationalError

    with mock.patch("grama.views.connection.ensure_connection", side_effect=OperationalError("db password=hunter2")):
        response = client.get("/api/v1/health/")
    assert response.status_code == 503
    assert set(response.json()) == {"status", "time"} and response.json()["status"] == "database_down"
    assert b"hunter2" not in response.content


def test_failed_jobs_of_the_last_24_hours_are_counted(client):
    from django_q.models import Task

    now = timezone.now()

    def job(name, success, hours_ago):
        at = now - timedelta(hours=hours_ago)
        Task.objects.create(id=name, name=name, func="apps.posts.tasks.run_escalation", started=at, stopped=at, success=success)

    assert client.get("/api/v1/health/").json()["failed_jobs_24h"] == 0
    job("failed-recent", False, 1)
    job("failed-recent-2", False, 23)
    job("failed-old", False, 30)
    job("succeeded", True, 1)
    assert client.get("/api/v1/health/").json()["failed_jobs_24h"] == 2

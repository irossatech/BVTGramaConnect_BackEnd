"""The health check says whether the background worker is running (BE_02, BE-REL-103).

A stopped qcluster means no pushes, no escalation and no translation, while the site looks fine.
The status code stays 200 either way: deploys call health/ before they start the worker.
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

"""Which days the audit screens show (owner decision of 2026-11-11; contract section 8).

Without dates: the last 90 days. With dates: as asked, never further back than 180 days. The same for
the list, its counts and the export. Older entries stay in the database. Exports over the row limit
answer 400 instead of a cut file. Expected values are worked out here from those rules.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import record

pytestmark = pytest.mark.django_db


def entry_days_ago(world, days, label):
    entry = record("user.blocked", actor=world.users["da_guntur"], target_label=label)
    AuditLog.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=days))
    return entry


@pytest.fixture
def aged(world):
    """One entry each: 10, 89, 91 and 200 days old."""
    for days in (10, 89, 91, 200):
        entry_days_ago(world, days, f"d{days}")


def labels(data):
    return sorted(row["target_label"] for row in data["results"])


def day(days_ago):
    return (timezone.localdate() - timedelta(days=days_ago)).isoformat()


def get(api, world, query=""):
    response = api(world.users["super"]).get(f"/api/v1/audit/{query}")
    assert response.status_code == 200, response.content
    return response.json()


def test_without_dates_the_last_90_days(api, world, aged):
    data = get(api, world)
    assert labels(data) == ["d10", "d89"]
    assert data["count"] == 2 and data["counts_by_type"]["users"] == 2
    assert data["window"] == {"from": day(90), "to": day(0)}


@pytest.mark.parametrize(
    "query, expected, window_from, window_to",
    [
        (f"?from={day(100)}", ["d10", "d89", "d91"], 100, 0),
        (f"?from={day(400)}", ["d10", "d89", "d91"], 180, 0),  # never before the 180-day limit
        (f"?to={day(50)}", ["d89", "d91"], 180, 50),  # only `to`: from the limit
        (f"?from={day(95)}&to={day(85)}", ["d89", "d91"], 95, 85),
        (f"?from={day(300)}&to={day(190)}", [], 180, 190),  # wholly before the limit: nothing
    ],
)
def test_chosen_dates_never_go_past_six_months(api, world, aged, query, expected, window_from, window_to):
    data = get(api, world, query)
    assert labels(data) == expected
    assert data["count"] == len(expected)
    assert data["window"] == {"from": day(window_from), "to": day(window_to)}


def test_older_entries_stay_in_the_database(api, world, aged):
    get(api, world, f"?from={day(400)}")
    assert AuditLog.objects.filter(target_label="d200").exists()


def test_search_works_inside_the_window(api, world, aged):
    assert labels(get(api, world, "?q=d9")) == []  # d91 is older than 90 days
    assert labels(get(api, world, f"?q=d9&from={day(120)}")) == ["d91"]


def export_labels(response):
    """Targets of the user.blocked rows (an earlier export's own report.exported row is left out)."""
    rows = [line.split(",") for line in response.content.decode("utf-8").splitlines()[1:]]
    return sorted(row[5] for row in rows if row[3] == "user.blocked")


def test_export_follows_the_same_days(api, world, aged):
    client = api(world.users["super"])
    assert export_labels(client.get("/api/v1/audit/export/?format=csv")) == ["d10", "d89"]
    old = client.get(f"/api/v1/audit/export/?format=csv&from={day(400)}")
    assert export_labels(old) == ["d10", "d89", "d91"]


def test_too_big_an_export_is_refused_and_not_audited(api, world, aged, settings):
    settings.AUDIT_EXPORT_MAX_ROWS = 1
    response = api(world.users["super"]).get("/api/v1/audit/export/?format=csv")
    assert response.status_code == 400
    error = json.loads(response.content)["error"]  # JSON text even when ?format=csv chose the CSV renderer
    assert error["code"] == "validation_error"
    assert "shorter date range" in error["message"] and set(error["fields"]) == {"from", "to"}
    assert not AuditLog.objects.filter(action="report.exported").exists()

    settings.AUDIT_EXPORT_MAX_ROWS = 2  # exactly the two rows of the default 90 days
    assert api(world.users["super"]).get("/api/v1/audit/export/?format=csv").status_code == 200
    assert AuditLog.objects.filter(action="report.exported").count() == 1


def test_bad_dates_are_still_400(api, world):
    client = api(world.users["super"])
    assert client.get("/api/v1/audit/?from=not-a-date").status_code == 400
    assert client.get("/api/v1/audit/?to=2026-13-40").status_code == 400
    assert client.get("/api/v1/audit/export/?format=csv&from=nope").status_code == 400

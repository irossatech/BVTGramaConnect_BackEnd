"""Which days the audit screens show (decision of 2026-11-20; contract section 8).

Without dates: every entry, however old, in pages. With dates (a range, or a month as its first and last
day): exactly those days. The same for the list, its counts and the export. Exports over the row limit
answer 400 instead of a cut file. Expected values are worked out here from those rules. (Before: the
last 180 days by default since 2026-11-19, 90 days and never more than 180 days back before that.)
"""

import json
from datetime import datetime, timedelta

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
    """One entry each: 10, 89, 170, 200 and 400 days old."""
    for days in (10, 89, 170, 200, 400):
        entry_days_ago(world, days, f"d{days}")


def labels(data):
    return sorted(row["target_label"] for row in data["results"] if row["target_label"].startswith("d"))


def day(days_ago):
    return (timezone.localdate() - timedelta(days=days_ago)).isoformat()


def get(api, world, query=""):
    response = api(world.users["super"]).get(f"/api/v1/audit/{query}")
    assert response.status_code == 200, response.content
    return response.json()


def test_without_dates_every_entry_however_old(api, world, aged):
    data = get(api, world)
    assert labels(data) == ["d10", "d170", "d200", "d400", "d89"]
    assert data["counts_by_type"]["users"] == 5
    assert data["window"] == {"from": day(400), "to": day(0)}  # from the oldest entry


@pytest.mark.parametrize(
    "query, expected, window_from, window_to",
    [
        (f"?from={day(100)}", ["d10", "d89"], 100, 0),
        (f"?from={day(450)}", ["d10", "d170", "d200", "d400", "d89"], 450, 0),  # older than 180 days too
        (f"?from={day(300)}&to={day(190)}", ["d200"], 300, 190),  # wholly older than 180 days
        (f"?from={day(175)}&to={day(165)}", ["d170"], 175, 165),
        (f"?to={day(150)}", ["d170", "d200", "d400"], 400, 150),  # only `to`: from the oldest entry
    ],
)
def test_chosen_dates_reach_any_old_entry(api, world, aged, query, expected, window_from, window_to):
    data = get(api, world, query)
    assert labels(data) == expected
    assert data["window"] == {"from": day(window_from), "to": day(window_to)}


def test_a_month_is_its_first_and_last_day(api, world):
    """The console's month picker sends a month as from = the 1st, to = the last day."""
    inside = record("user.blocked", actor=world.users["da_guntur"], target_label="d-march")
    outside = record("user.blocked", actor=world.users["da_guntur"], target_label="d-april")
    tz = timezone.get_current_timezone()
    AuditLog.objects.filter(pk=inside.pk).update(created_at=timezone.make_aware(datetime(2025, 3, 31, 23, 30), tz))
    AuditLog.objects.filter(pk=outside.pk).update(created_at=timezone.make_aware(datetime(2025, 4, 1, 0, 30), tz))
    assert labels(get(api, world, "?from=2025-03-01&to=2025-03-31")) == ["d-march"]
    assert labels(get(api, world, "?from=2025-04-01&to=2025-04-30")) == ["d-april"]


def test_search_finds_old_entries_too(api, world, aged):
    assert labels(get(api, world, "?q=d4")) == ["d400"]
    assert labels(get(api, world, f"?q=d4&from={day(250)}")) == []  # dates still narrow the search


def export_labels(response):
    """Targets of the user.blocked rows (an earlier export's own report.exported row is left out)."""
    rows = [line.split(",") for line in response.content.decode("utf-8").splitlines()[1:]]
    return sorted(row[5] for row in rows if row[3] == "user.blocked" and row[5].startswith("d"))


def test_export_follows_the_same_days(api, world, aged):
    client = api(world.users["super"])
    assert export_labels(client.get("/api/v1/audit/export/?format=csv")) == ["d10", "d170", "d200", "d400", "d89"]
    old = client.get(f"/api/v1/audit/export/?format=csv&from={day(450)}&to={day(190)}")
    assert export_labels(old) == ["d200", "d400"]


def test_too_big_an_export_is_refused_and_not_audited(api, world, aged, settings):
    settings.AUDIT_EXPORT_MAX_ROWS = 1
    response = api(world.users["super"]).get(f"/api/v1/audit/export/?format=csv&from={day(450)}")
    assert response.status_code == 400
    error = json.loads(response.content)["error"]  # JSON text even when ?format=csv chose the CSV renderer
    assert error["code"] == "validation_error"
    assert "shorter date range" in error["message"] and set(error["fields"]) == {"from", "to"}
    assert not AuditLog.objects.filter(action="report.exported").exists()

    settings.AUDIT_EXPORT_MAX_ROWS = 50000
    assert api(world.users["super"]).get(f"/api/v1/audit/export/?format=csv&from={day(450)}").status_code == 200
    assert AuditLog.objects.filter(action="report.exported").count() == 1


def test_bad_dates_are_still_400(api, world):
    client = api(world.users["super"])
    assert client.get("/api/v1/audit/?from=not-a-date").status_code == 400
    assert client.get("/api/v1/audit/?to=2026-13-40").status_code == 400
    assert client.get("/api/v1/audit/export/?format=csv&from=nope").status_code == 400

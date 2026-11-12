"""Audit list and export read less from the database (audit BE-DB-002).

The list gets its page total from the per-type counts instead of a second COUNT(*); the export reads
only the file's columns. The expected values are worked out here from the entries the tests write.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.geography.areas import AreaRef

pytestmark = pytest.mark.django_db


def entries(world, how_many, action, *, actor="da_guntur", minutes_ago=0, **fields):
    """`how_many` entries of `action`, a minute apart, the newest `minutes_ago` minutes old."""
    made = []
    for index in range(how_many):
        entry = record(action, actor=world.users[actor] if actor else None, **fields)
        AuditLog.objects.filter(pk=entry.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago + index)
        )
        made.append(entry)
    return made


@pytest.fixture
def log(world):
    entries(world, 12, "auth.login")  # logins
    entries(world, 7, "user.blocked", target_label="Gopal", minutes_ago=100)  # users
    entries(world, 4, "post.published", actor="ma_tenali", target_label="గ్రామ సభ", minutes_ago=200)  # posts
    entries(world, 2, "push.failed", actor=None, minutes_ago=60 * 24 * 10)  # system, ten days old


def get(api, world, query=""):
    response = api(world.users["super"]).get(f"/api/v1/audit/{query}")
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    "query, total",
    [
        ("", 25),
        ("?type=logins", 12),
        ("?type=users", 7),
        ("?type=posts", 4),
        ("?type=system", 2),
        ("?type=areas", 0),
        ("?type=nonsense", 0),
        ("?q=గ్రామ", 4),
        ("?q=గ్రామ&type=logins", 0),
        ("?action=user.blocked", 7),
    ],
)
def test_page_total_matches_the_entries(api, world, log, query, total):
    data = get(api, world, query)
    assert data["count"] == total
    assert len(data["results"]) == min(total, 20)


def test_counts_by_type_ignore_the_tab_and_follow_the_other_filters(api, world, log):
    expected = {"logins": 12, "users": 7, "posts": 4, "areas": 0, "settings": 0, "system": 2}
    assert get(api, world)["counts_by_type"] == expected
    assert get(api, world, "?type=users")["counts_by_type"] == expected
    today = timezone.localdate().isoformat()
    recent = get(api, world, f"?from={today}")
    assert recent["counts_by_type"]["system"] == 0 and recent["count"] == 23
    actor = get(api, world, f"?actor={world.users['ma_tenali'].id}")
    assert actor["count"] == 4 and actor["counts_by_type"]["posts"] == 4


def test_pages_and_links_still_work(api, world, log):
    first = get(api, world, "?type=logins&page_size=5")
    assert first["count"] == 12 and len(first["results"]) == 5 and first["previous"] is None
    last = get(api, world, "?type=logins&page_size=5&page=3")
    assert len(last["results"]) == 2 and last["next"] is None
    newest_first = [row["at"] for row in get(api, world, "?page_size=100")["results"]]
    assert newest_first == sorted(newest_first, reverse=True)
    assert api(world.users["super"]).get("/api/v1/audit/?page=99").status_code == 404


def test_the_list_counts_once(api, world, log):
    client = api(world.users["super"])
    with CaptureQueriesContext(connection) as ctx:
        assert client.get("/api/v1/audit/?type=users").status_code == 200
    counts = [q["sql"] for q in ctx.captured_queries if "audit_auditlog" in q["sql"] and "COUNT(" in q["sql"]]
    assert len(counts) == 1 and "GROUP BY" in counts[0]


def test_export_holds_every_matching_entry_in_order(api, world):
    entries(world, 1, "user.blocked", target_label="గోపాల్", area=AreaRef("VILLAGE", world.ref("kaza").id), minutes_ago=5)
    entries(world, 1, "push.failed", actor=None, minutes_ago=10)
    entries(world, 1, "post.published", actor="ma_tenali", target_label="=1+1", minutes_ago=15)
    response = api(world.users["super"]).get("/api/v1/audit/export/?format=csv")
    assert response.status_code == 200
    lines = response.content.decode("utf-8").splitlines()
    assert lines[0] == "﻿time,user,role,action,type,target,area_level,area_id,via"
    body = [line.split(",", 1)[1] for line in lines[1:]]  # without the time
    kaza = world.ref("kaza").id
    assert body[0] == f"da_guntur,DISTRICT_ADMIN,user.blocked,users,గోపాల్,VILLAGE,{kaza},app"
    assert body[1] == "System,,push.failed,system,,,,app"
    assert body[2] == "ma_tenali,MANDAL_ADMIN,post.published,posts,'=1+1,,,app"  # formula-safe
    assert len(body) == 3  # the export's own report.exported entry is written after the file


def test_export_reads_only_its_columns(api, world, settings):
    entries(world, 5, "auth.login")
    settings.AUDIT_EXPORT_MAX_ROWS = 5  # exactly at the limit: still a full file
    client = api(world.users["super"])
    with CaptureQueriesContext(connection) as ctx:
        response = client.get("/api/v1/audit/export/?format=csv")
    assert len(response.content.decode("utf-8").splitlines()) == 1 + 5
    reads = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("SELECT") and "audit_auditlog" in q["sql"]]
    assert reads and all('"data"' not in sql for sql in reads)

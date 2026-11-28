"""The contacts import costs the same number of queries for 10 rows or 150 (audit BE_01, BE-PERF-004)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.contacts.models import Contact
from apps.contacts.services import import_contacts

from .test_import import HEADER

pytestmark = pytest.mark.django_db


def file_with(rows: int, start: int = 0) -> str:
    lines = [
        f"revenue,,Office {n},,0864422{n:04d},,Guntur,Tenali,{'Kolakaluru' if n % 2 else 'Nandivelugu'},,\n"
        for n in range(start, start + rows)
    ]
    return HEADER + "".join(lines)


def queries(actor, text) -> int:
    with CaptureQueriesContext(connection) as captured:
        report = import_contacts(actor, text)
    assert report.ok, report.errors
    return len(captured)


def test_import_cost_does_not_grow_with_the_rows(world, settings):
    settings.CONTACTS_MAX_PER_AREA = 1000
    actor = world.users["super"]
    small = queries(actor, file_with(10))
    large = queries(actor, file_with(150, start=1000))
    # 140 more rows; before this fix that meant 600 or more more queries. SQLite allows 999
    # parameters per statement, so bulk_create may split the INSERT into a few: never one per row.
    assert large - small <= 4
    assert Contact.objects.count() == 160


def test_a_second_import_updates_in_bulk(world, settings):
    settings.CONTACTS_MAX_PER_AREA = 1000
    actor = world.users["super"]
    import_contacts(actor, file_with(20))
    renamed = file_with(20).replace("Office ", "Revenue Office ")
    report = import_contacts(actor, renamed)
    assert (report.created, report.updated, report.skipped) == (0, 20, 0)
    assert Contact.objects.filter(name__startswith="Revenue Office").count() == 20

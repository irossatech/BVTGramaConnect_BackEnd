"""Migrations apply forward and backward on an empty database (audit stage S6, BVTGC-17).

The deploy scripts migrate forward; a rollback (deploy/qa/BVT-deploy.sh --rollback, RUNBOOK) migrates
back. Here, in a separate process on its own temporary SQLite file (run_manage, which checks the database
before it runs anything, after the qa incident of 2026-11-25):

- every run: everything forward, then each app's newest migration back one step and forward again (what
  a rollback of the latest release undoes);
- on request (RUN_MIGRATION_ROUND_TRIP=1, about 1 h 45 min on a laptop): every app back to zero and
  forward again. It passed on 2026-11-26 (BE_06 report V2);
- every data step (RunPython, RunSQL) of our apps has a reverse step, so a rollback never stops half
  way (.claude/conventions.md, "Data migrations").
"""

from __future__ import annotations

import os

import pytest

from apps.accounts.tests.test_sync_admin import run_manage

#: Our apps that have migration files, roughly dependents first (Django unapplies other dependents itself).
LOCAL_APPS = ["wallet", "bills", "ads", "service_requests", "contacts", "citizens", "web_admin", "web_public", "notifications",
              "audit", "posts", "geography", "accounts"]

PREAMBLE = """
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

def unapplied():
    executor = MigrationExecutor(connection)
    return executor.migration_plan(executor.loader.graph.leaf_nodes())

call_command("migrate", verbosity=0, interactive=False)
assert not unapplied(), "forward left migrations unapplied"
"""

NEWEST_BACK_AND_FORTH = PREAMBLE + f"""
on_disk = MigrationExecutor(connection).loader.disk_migrations
for app in {LOCAL_APPS!r}:
    names = sorted(name for label, name in on_disk if label == app)
    previous = names[-2] if len(names) > 1 else "zero"
    call_command("migrate", app, previous, verbosity=0, interactive=False)
    call_command("migrate", verbosity=0, interactive=False)
    assert not unapplied(), f"{{app}}: forward again left migrations unapplied"
print("NEWEST_OK")
"""

ROUND_TRIP = PREAMBLE + f"""
for app in {LOCAL_APPS!r}:
    call_command("migrate", app, "zero", verbosity=0, interactive=False)
left = sorted(f"{{a}}.{{n}}" for a, n in MigrationExecutor(connection).loader.applied_migrations if a in {LOCAL_APPS!r})
assert not left, f"still applied after zero: {{left}}"
call_command("migrate", verbosity=0, interactive=False)
assert not unapplied(), "the second forward left migrations unapplied"
print("ROUND_TRIP_OK")
"""


def test_each_apps_newest_migration_goes_back_and_forward(tmp_path):
    result = run_manage(tmp_path / "newest.sqlite3", "shell", "-c", NEWEST_BACK_AND_FORTH)
    assert result.returncode == 0 and "NEWEST_OK" in result.stdout, (result.stdout[-1500:], result.stderr[-3000:])


@pytest.mark.skipif(os.environ.get("RUN_MIGRATION_ROUND_TRIP") != "1",
                    reason="every app to zero and back takes about 1 h 45 min; set RUN_MIGRATION_ROUND_TRIP=1")
def test_every_migration_goes_forward_back_to_zero_and_forward_again(tmp_path):
    result = run_manage(tmp_path / "round_trip.sqlite3", "shell", "-c", ROUND_TRIP)
    assert result.returncode == 0 and "ROUND_TRIP_OK" in result.stdout, (result.stdout[-1500:], result.stderr[-3000:])


def test_every_data_step_of_our_apps_has_a_reverse():
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.operations.special import RunPython, RunSQL

    loader = MigrationLoader(None, ignore_no_migrations=True)
    one_way = []
    for (app, name), migration in sorted(loader.disk_migrations.items()):
        if app not in LOCAL_APPS:
            continue
        for operation in migration.operations:
            if (isinstance(operation, RunPython) and operation.reverse_code is None) or \
                    (isinstance(operation, RunSQL) and operation.reverse_sql is None):
                one_way.append(f"{app}.{name}")
    assert not one_way, f"Data steps without a reverse (give one, RunPython.noop if nothing to undo): {one_way}"


def test_the_list_names_every_one_of_our_apps_with_migration_files():
    from django.apps import apps
    from django.db.migrations.loader import MigrationLoader

    ours = {config.label for config in apps.get_app_configs() if config.name.startswith("apps.")}
    with_files = {label for label, _name in MigrationLoader(None, ignore_no_migrations=True).disk_migrations}
    assert set(LOCAL_APPS) == ours & with_files

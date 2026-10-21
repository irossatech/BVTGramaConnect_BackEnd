"""sync_role_permissions, the data migration from role strings, and Django admin for groups."""

import os
import sqlite3
import subprocess
import sys
from io import StringIO

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management import CommandError, call_command
from django.test import Client

from apps.accounts.models import RoleAssignment, RoleGroup, User
from apps.accounts.sync import sync_role_permissions
from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


def perm(name):
    app, codename = name.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


# --- The sync -------------------------------------------------------------------------------------


def test_sync_is_idempotent(db):
    assert sync_role_permissions() == []
    assert sync_role_permissions() == []


def test_sync_puts_back_hand_edits(db):
    group = RoleGroup.objects.get(key="VILLAGE_ADMIN").group
    group.permissions.remove(perm("posts.publish_post"))
    group.permissions.add(perm("web_admin.access_web_console"))
    RoleGroup.objects.filter(key="VILLAGE_ADMIN").update(level="MANDAL")
    changes = sync_role_permissions()
    assert sorted(changes) == sorted([
        "VILLAGE_ADMIN: level 'MANDAL' -> 'VILLAGE'",
        "VILLAGE_ADMIN: + posts.publish_post",
        "VILLAGE_ADMIN: - web_admin.access_web_console",
    ])
    assert sync_role_permissions() == []


def test_sync_recreates_missing_groups(db):
    RoleAssignment.objects.all().delete()
    RoleGroup.objects.filter(key="PANCHAYAT_MEMBER").delete()
    Group.objects.filter(name="Panchayat Member").delete()
    changes = sync_role_permissions()
    assert "PANCHAYAT_MEMBER: created group 'Panchayat Member'" in changes
    assert RoleGroup.objects.get(key="PANCHAYAT_MEMBER").group.permissions.count() == 3


def test_command_reports_audits_once_and_checks(db):
    group = RoleGroup.objects.get(key="MANDAL_ADMIN").group
    group.permissions.remove(perm("posts.mark_urgent"))

    with pytest.raises(CommandError):
        call_command("sync_role_permissions", "--check", stdout=StringIO())
    assert not group.permissions.filter(codename="mark_urgent").exists()  # --check changed nothing

    out = StringIO()
    call_command("sync_role_permissions", stdout=out)
    assert "MANDAL_ADMIN: + posts.mark_urgent" in out.getvalue()
    entry = AuditLog.objects.get(action="setting.role_permissions_synced")
    assert entry.data == {"changes": ["MANDAL_ADMIN: + posts.mark_urgent"]} and entry.via == "system"
    assert not AuditLog.objects.filter(action="setting.group_permissions_changed").exclude(data__permissions=["posts.mark_urgent"]).exists()

    again = StringIO()
    call_command("sync_role_permissions", stdout=again)
    assert "already match" in again.getvalue()
    call_command("sync_role_permissions", "--check", stdout=StringIO())  # no error now


def test_group_permission_changes_outside_the_sync_are_audited(db):
    group = RoleGroup.objects.get(key="DISTRICT_ADMIN").group
    group.permissions.add(perm("audit.view_audit_log"))
    entry = AuditLog.objects.get(action="setting.group_permissions_changed")
    assert entry.target_label == "District Admin"
    assert entry.data == {"change": "added", "permissions": ["audit.view_audit_log"]}
    group.permissions.clear()
    removed = AuditLog.objects.filter(action="setting.group_permissions_changed", data__change="removed").get()
    assert len(removed.data["permissions"]) == 19


# --- Django admin ----------------------------------------------------------------------------------


def staff(user):
    User.objects.filter(pk=user.pk).update(is_staff=True)
    browser = Client()
    browser.force_login(User.objects.get(pk=user.pk))
    return browser


def test_only_super_admins_edit_groups_and_assignments_in_django_admin(world):
    group = RoleGroup.objects.get(key="VILLAGE_ADMIN").group
    assignment = RoleAssignment.objects.filter(user=world.users["pm_kaza"]).get()
    district = staff(world.users["da_guntur"])
    for url in (
        "/django-admin/auth/group/",
        f"/django-admin/auth/group/{group.pk}/change/",
        "/django-admin/accounts/roleassignment/",
        f"/django-admin/accounts/roleassignment/{assignment.pk}/change/",
    ):
        assert district.get(url).status_code == 403, url
    super_admin = staff(world.users["super"])
    for url in ("/django-admin/auth/group/", f"/django-admin/auth/group/{group.pk}/change/", "/django-admin/accounts/roleassignment/"):
        assert super_admin.get(url).status_code == 200, url


def test_developer_superuser_without_the_role_cannot_edit_groups(world):
    developer = User.objects.create_superuser(username="developer", name="Dev", password="x")
    browser = Client()
    browser.force_login(developer)
    group = RoleGroup.objects.get(key="VILLAGE_ADMIN").group
    page = browser.get(f"/django-admin/auth/group/{group.pk}/change/")
    # A Django superuser may look, but the form is read-only: the matrix belongs to the Super Admin role.
    assert page.status_code == 200
    response = browser.post(f"/django-admin/auth/group/{group.pk}/change/", {"name": "Hacked", "permissions": []})
    assert response.status_code == 403
    assert Group.objects.filter(name="Village Admin").exists()


def test_group_edit_in_django_admin_is_audited_with_the_editor(world):
    group = RoleGroup.objects.get(key="PANCHAYAT_MEMBER").group
    kept = list(group.permissions.values_list("pk", flat=True))
    browser = staff(world.users["super"])
    response = browser.post(
        f"/django-admin/auth/group/{group.pk}/change/",
        {"name": group.name, "permissions": [*kept, perm("posts.mark_urgent").pk]},
    )
    assert response.status_code == 302
    entries = AuditLog.objects.filter(action="setting.group_permissions_changed", target_id=str(group.pk))
    added = [e for e in entries if "posts.mark_urgent" in e.data["permissions"] and e.data["change"] == "added"]
    assert added and added[0].actor == world.users["super"] and added[0].via == "admin"


# --- The data migration from role strings (0005) -----------------------------------------------------


def run_manage(db_path, *args):
    env = {**os.environ, "SQLITE_PATH": str(db_path), "DJANGO_SETTINGS_MODULE": "grama.settings.dev"}
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )


def test_migration_turns_role_strings_into_groups(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    first = run_manage(db_path, "migrate", "accounts", "0003", "--noinput")
    assert first.returncode == 0, first.stderr
    rows = [
        # id, username, status, role, area level, area id
        ("11111111111111111111111111111111", "old.super", "ACTIVE", "SUPER_ADMIN", "STATE", 1),
        ("22222222222222222222222222222222", "old.village", "ACTIVE", "VILLAGE_ADMIN", "VILLAGE", 5),
        ("33333333333333333333333333333333", "old.member", "BLOCKED", "PANCHAYAT_MEMBER", "VILLAGE", 5),
    ]
    with sqlite3.connect(db_path) as conn:
        for user_id, username, status, role, level, area_id in rows:
            conn.execute(
                "INSERT INTO accounts_user (id, password, is_superuser, name, name_te, status, preferred_language,"
                " is_staff, created_at, updated_at, username, must_change_password, token_version)"
                " VALUES (?, '!', 0, ?, '', ?, 'te', 0, '2026-10-01 00:00:00', '2026-10-01 00:00:00', ?, 0, 0)",
                (user_id, username, status, username),
            )
            conn.execute(
                "INSERT INTO accounts_roleassignment (id, user_id, role, area_level, area_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, '2026-10-01 00:00:00')",
                (user_id[::-1], user_id, role, level, area_id),
            )
        conn.execute(
            "INSERT INTO accounts_roleassignment (id, user_id, role, area_level, area_id, created_at)"
            " VALUES ('44444444444444444444444444444444', '22222222222222222222222222222222', 'PANCHAYAT_MEMBER', 'VILLAGE', 6, '2026-10-01 00:00:00')"
        )
    second = run_manage(db_path, "migrate", "--noinput")
    assert second.returncode == 0, second.stderr
    assert "Role assignments before (role strings): 4 (PANCHAYAT_MEMBER 2, SUPER_ADMIN 1, VILLAGE_ADMIN 1)" in second.stdout
    assert "Role assignments after (groups): 4 (PANCHAYAT_MEMBER 2, SUPER_ADMIN 1, VILLAGE_ADMIN 1)" in second.stdout
    assert "Blocked users: 1 before, 1 after" in second.stdout
    with sqlite3.connect(db_path) as conn:
        pairs = conn.execute(
            "SELECT u.username, rg.key FROM accounts_roleassignment ra JOIN accounts_user u ON u.id = ra.user_id"
            " JOIN accounts_rolegroup rg ON rg.group_id = ra.group_id ORDER BY u.username, rg.key"
        ).fetchall()
        assert pairs == [
            ("old.member", "PANCHAYAT_MEMBER"),
            ("old.super", "SUPER_ADMIN"),
            ("old.village", "PANCHAYAT_MEMBER"),
            ("old.village", "VILLAGE_ADMIN"),
        ]
        members = conn.execute(
            "SELECT u.username, g.name FROM accounts_user_groups ug JOIN accounts_user u ON u.id = ug.user_id"
            " JOIN auth_group g ON g.id = ug.group_id ORDER BY u.username, g.name"
        ).fetchall()
        assert members == [
            ("old.member", "Panchayat Member"),
            ("old.super", "Super Admin"),
            ("old.village", "Panchayat Member"),
            ("old.village", "Village Admin"),
        ]
        assert conn.execute("SELECT username FROM accounts_user WHERE is_active = 0").fetchall() == [("old.member",)]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts_user)")}
        assert "status" not in columns and "is_active" in columns
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "accounts_roledefaultpassword" not in tables and "accounts_loginattempt" not in tables
        assert "axes_accessattempt" in tables
    again = run_manage(db_path, "sync_role_permissions")
    assert again.returncode == 0 and "already match" in again.stdout


# --- The first Super Admin -----------------------------------------------------------------------


def test_create_super_admin_makes_the_first_one(geo):
    out = StringIO()
    call_command("create_super_admin", "--username", "Sita.Admin", "--name", "Sita", stdout=out)
    user = User.objects.get(username="sita.admin")
    password = out.getvalue().split("first login): ")[1].strip()
    assert user.check_password(password) and user.must_change_password
    assert list(user.groups.values_list("role_group__key", flat=True)) == ["SUPER_ADMIN"]
    from apps.accounts import authz

    user.must_change_password = False
    assert authz.can(user, "web_admin.access_web_console")
    call_command("create_super_admin", "--username", "sita.admin", stdout=StringIO())  # again: no change
    assert user.role_assignments.count() == 1
    assert user.__class__.objects.get(pk=user.pk).check_password(password)


def test_create_super_admin_needs_a_name_for_new_users(geo):
    with pytest.raises(CommandError):
        call_command("create_super_admin", "--username", "nobody.new", stdout=StringIO())

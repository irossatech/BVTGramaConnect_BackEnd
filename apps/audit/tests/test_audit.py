"""Audit log: written for logins and user actions, read and exported by Super Admins only."""

import pytest

from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.geography.areas import AreaRef

pytestmark = pytest.mark.django_db


def test_record_fills_actor_type_and_area(world):
    user = world.users["va_kolakaluru"]
    entry = record("user.blocked", actor=user, target=world.users["pm_kolakaluru"], target_label="pm", area=world.ref("kolakaluru"))
    assert entry.type == "users"
    assert entry.actor_name == "va_kolakaluru"
    assert entry.actor_role == "VILLAGE_ADMIN"
    assert entry.target_type == "accounts.user"
    assert (entry.area_level, entry.area_id) == ("VILLAGE", world.ref("kolakaluru").id)
    assert record("email.failed", via="system", data={"to": "ra***@example.org"}).type == "system"


def test_entries_cannot_be_changed_or_deleted(world):
    entry = record("auth.login", actor=world.users["super"])
    entry.action = "something else"
    with pytest.raises(RuntimeError):
        entry.save()
    with pytest.raises(RuntimeError):
        entry.delete()


def test_user_actions_are_audited(api, world):
    client = api(world.users["va_kolakaluru"])
    target = world.users["pm_kolakaluru"]
    client.post(f"/api/v1/users/{target.id}/block/", {"reason": "spam"}, format="json")
    client.post(f"/api/v1/users/{target.id}/unblock/", {}, format="json")
    client.post(
        "/api/v1/users/",
        {"name": "New", "username": "new.official", "role": "PANCHAYAT_MEMBER", "area": world.area_json("kolakaluru")},
        format="json",
    )
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"user.blocked", "user.unblocked", "user.created"} <= actions
    assert AuditLog.objects.get(action="user.blocked").data == {"reason": "spam"}


def test_login_is_audited(api, world):
    from conftest import TEST_PASSWORD

    user = world.users["va_kolakaluru"]
    api().post("/api/v1/auth/login/", {"username": user.username, "password": TEST_PASSWORD}, format="json")
    entry = AuditLog.objects.get(action="auth.login")
    assert entry.actor == user and entry.type == "logins" and entry.via == "app"


def test_list_filters_and_counts(api, world):
    record("auth.login", actor=world.users["da_guntur"])
    record("user.blocked", actor=world.users["da_guntur"], target_label="Gopal", area=AreaRef("VILLAGE", world.ref("kaza").id))
    record("post.published", actor=world.users["ma_tenali"], target_label="గ్రామ సభ")
    client = api(world.users["super"])

    everything = client.get("/api/v1/audit/").json()
    assert everything["count"] == 3
    assert everything["counts_by_type"]["logins"] == 1
    assert everything["counts_by_type"]["users"] == 1
    assert everything["counts_by_type"]["posts"] == 1

    users = client.get("/api/v1/audit/?type=users").json()
    assert [row["action"] for row in users["results"]] == ["user.blocked"]
    assert users["results"][0]["area"]["name"] == "కాజా"
    assert users["counts_by_type"]["posts"] == 1  # counts ignore the type filter

    search = client.get("/api/v1/audit/?q=గ్రామ").json()
    assert [row["target_label"] for row in search["results"]] == ["గ్రామ సభ"]

    by_actor = client.get(f"/api/v1/audit/?actor={world.users['ma_tenali'].id}").json()
    assert by_actor["count"] == 1
    assert client.get("/api/v1/audit/?from=not-a-date").status_code == 400


def test_audit_is_for_super_admins_only(api, world):
    assert api(world.users["da_guntur"]).get("/api/v1/audit/").status_code == 403
    assert api(world.users["da_guntur"]).get("/api/v1/audit/export/").status_code == 403
    assert api().get("/api/v1/audit/").status_code == 401


def test_csv_export_keeps_telugu_and_is_audited(api, world):
    record("post.published", actor=world.users["ma_tenali"], target_label="గ్రామ సభ")
    response = api(world.users["super"]).get("/api/v1/audit/export/?format=csv")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert text.startswith("﻿time,user,role,action")
    assert "గ్రామ సభ" in text
    assert AuditLog.objects.filter(action="report.exported").exists()

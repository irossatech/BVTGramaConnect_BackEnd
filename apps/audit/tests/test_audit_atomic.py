"""A change and its audit entry are saved together or not at all (BE_02, BE-ARC-101).

Each test makes the audit write fail and checks that the change itself was not kept.
"""

import pytest

from apps.notifications.models import NotificationTemplate
from apps.posts.models import Category, SettingOverride

pytestmark = pytest.mark.django_db


class AuditDown(Exception):
    pass


def fail(*args, **kwargs):
    raise AuditDown()


def test_category_change_is_undone_when_its_audit_entry_fails(api, world, monkeypatch):
    news = Category.objects.get(key="news")
    monkeypatch.setattr("apps.posts.views.record", fail)
    with pytest.raises(AuditDown):
        api(world.users["super"]).patch(f"/api/v1/admin/categories/{news.id}/", {"is_active": False}, format="json")
    news.refresh_from_db()
    assert news.is_active is True


def test_new_category_is_undone_when_its_audit_entry_fails(api, world, monkeypatch):
    monkeypatch.setattr("apps.posts.views.record", fail)
    body = {"key": "events", "name_te": "కార్యక్రమాలు", "name_en": "Events", "short_name_te": "కార్య", "short_name_en": "Events", "order": 9}
    with pytest.raises(AuditDown):
        api(world.users["super"]).post("/api/v1/admin/categories/", body, format="json")
    assert not Category.objects.filter(key="events").exists()


def test_settings_change_is_undone_when_its_audit_entry_fails(api, world, monkeypatch):
    monkeypatch.setattr("apps.posts.views.record", fail)
    with pytest.raises(AuditDown):
        api(world.users["super"]).patch("/api/v1/admin/settings/", {"review_escalation_hours": 48}, format="json")
    assert not SettingOverride.objects.filter(key="REVIEW_ESCALATION_HOURS").exists()


def test_template_change_is_undone_when_its_audit_entry_fails(api, world, monkeypatch):
    template = NotificationTemplate.objects.filter(editable=True).order_by("key").first()
    before = (template.text_te, template.text_en)
    monkeypatch.setattr("apps.notifications.views.record", fail)
    with pytest.raises(AuditDown):
        api(world.users["super"]).patch(
            f"/api/v1/admin/notification-templates/{template.key}/",
            {"text_te": template.text_te + " ", "text_en": template.text_en + " !"},
            format="json",
        )
    template.refresh_from_db()
    assert (template.text_te, template.text_en) == before

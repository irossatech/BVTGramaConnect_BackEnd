"""Audit entries never keep more than AUDIT_TEXT_MAX_CHARS characters of any text (BE_02, BE-SEC-107).

The audit log is never cleaned, so text that something lets through by mistake is cut, not kept.
Comments and notes are limited to 500 characters by the API, so they are never cut.
"""

import pytest

from apps.audit.models import AuditLog
from apps.audit.services import CUT_MARK, record
from apps.posts.models import Category

pytestmark = pytest.mark.django_db


def test_short_text_and_other_values_are_kept_as_they_are(settings):
    settings.AUDIT_TEXT_MAX_CHARS = 2000
    comment = "క" * 500  # the longest comment the API accepts
    data = {"comment": comment, "days": 7, "urgent": True, "none": None}
    assert record("post.rechecked", data=data).data == data


def test_long_text_is_cut_with_a_mark(settings):
    settings.AUDIT_TEXT_MAX_CHARS = 20
    entry = record("category.updated", data={"name_en": "x" * 50})
    assert entry.data["name_en"] == "x" * 19 + CUT_MARK
    assert len(entry.data["name_en"]) == 20


def test_telugu_text_is_cut_by_characters_and_stays_readable(settings):
    settings.AUDIT_TEXT_MAX_CHARS = 10
    text = "గ్రామ చాటింపులు గ్రామ చాటింపులు"
    entry = record("post.discarded", data={"comment": text})
    entry.refresh_from_db()  # stored and read back from the database
    assert entry.data["comment"] == text[:9] + CUT_MARK
    assert len(entry.data["comment"]) == 10


def test_text_inside_lists_and_nested_objects_is_cut(settings):
    settings.AUDIT_TEXT_MAX_CHARS = 10
    entry = record("setting.changed", data={"changes": {"a": "y" * 30}, "items": ["z" * 30, 5]})
    assert entry.data == {"changes": {"a": "y" * 9 + CUT_MARK}, "items": ["z" * 9 + CUT_MARK, 5]}


def test_category_update_audits_only_the_accepted_fields(api, world):
    news = Category.objects.get(key="news")
    junk = "j" * 10_000
    response = api(world.users["super"]).patch(
        f"/api/v1/admin/categories/{news.id}/",
        {"is_active": False, "order": 9, "not_a_field": junk, "id": 12345},
        format="json",
    )
    assert response.status_code == 200
    entry = AuditLog.objects.filter(action="category.updated").latest("created_at")
    assert entry.data == {"is_active": False, "order": 9}

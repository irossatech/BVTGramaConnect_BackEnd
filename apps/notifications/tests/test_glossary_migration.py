"""notifications.0014: the recheck push takes the glossary's word, but never over a Super Admin's edit (BVTGC-67)."""

import importlib

import pytest
from django.apps import apps

from apps.notifications.models import NotificationTemplate
from apps.notifications.templates_data import DEFAULTS

migration = importlib.import_module("apps.notifications.migrations.0014_recheck_template_glossary_words")
pytestmark = pytest.mark.django_db


def recheck():
    return NotificationTemplate.objects.get(key="post_recheck")


def test_a_fresh_database_has_the_glossary_words():
    row = recheck()
    assert (row.name_te, row.text_te, row.text_en) == (
        "మళ్ళీ తనిఖీ", "మీ పోస్ట్ మళ్ళీ తనిఖీకి వచ్చింది: {comment}", "Your post needs a recheck: {comment}",
    )
    default = next(item for item in DEFAULTS if item["key"] == "post_recheck")
    assert {field: default[field] for field in migration.NEW} == migration.NEW  # fallback text agrees


def test_old_defaults_are_updated_and_edited_texts_are_kept():
    NotificationTemplate.objects.filter(key="post_recheck").update(
        name_te=migration.OLD["name_te"], text_te=migration.OLD["text_te"], text_en="Please fix your post: {comment}",
    )
    migration.forward(apps, None)
    row = recheck()
    assert row.name_te == migration.NEW["name_te"]
    assert row.text_te == migration.NEW["text_te"]
    assert row.text_en == "Please fix your post: {comment}"  # the Super Admin's own text stays

    migration.backward(apps, None)
    row = recheck()
    assert (row.name_te, row.text_te) == (migration.OLD["name_te"], migration.OLD["text_te"])
    assert row.text_en == "Please fix your post: {comment}"

"""Edit and delete a post category in the console (contract section 5, "Categories").

PATCH /admin/categories/{id}/ changes the names, short names and icon only while the category is
switched off; on, it answers 409 switch_off_first (2026-11-26). The switch itself and the order work
any time. The key never changes once added (400 validation_error on `key`).
DELETE /admin/categories/{id}/ removes a switched-off category no post uses, such as one added by
mistake: 204, audited as category.deleted, its icon file removed after the delete is saved. Switched
on: 409 switch_off_first. With posts: 409 category_in_use with `count`, and it stays (posts are kept
permanently and every post keeps its category). Only holders of posts.manage_categories (the Super
Admin) may do either; everyone else gets 403 permission_denied.
"""

import io
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from apps.posts import views
from apps.posts.models import Category, Post, PostStatus
from conftest import OFFICIALS, make_bare_post

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/categories/"
PUBLIC = "/api/v1/categories/"
NEW = {"key": "mistake", "name_te": "తప్పు", "name_en": "Added by mistake"}


def icon() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), (31, 138, 59)).save(buffer, "PNG")
    return SimpleUploadedFile("icon.png", buffer.getvalue(), content_type="image/png")


def off(key: str) -> Category:
    Category.objects.filter(key=key).update(is_active=False)
    return Category.objects.get(key=key)


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def added_off(admin) -> int:
    """A category added by mistake, then switched off: what the console lets the Super Admin delete."""
    category_id = admin.post(ADMIN, NEW, format="json").json()["id"]
    assert admin.patch(f"{ADMIN}{category_id}/", {"is_active": False}, format="json").status_code == 200
    return category_id


def a_post_in(world, category) -> Post:
    ref = world.ref("kolakaluru")
    return make_bare_post(
        author=world.users["pm_kolakaluru"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level=ref.level,
        posted_as_area_id=ref.id, category=category, title="శీర్షిక", body="b",
        target_level=ref.level, target_area_id=ref.id, status=PostStatus.PUBLISHED,
    )


# --- Delete ------------------------------------------------------------------------------------------

def test_a_switched_off_category_without_posts_is_deleted_and_audited(admin):
    category_id = added_off(admin)
    response = admin.delete(f"{ADMIN}{category_id}/")
    assert response.status_code == 204
    assert not Category.objects.filter(pk=category_id).exists()
    entry = AuditLog.objects.get(action="category.deleted")
    assert entry.target_id == str(category_id) and entry.target_label == "Added by mistake"
    assert entry.data == {"key": "mistake", "name_en": "Added by mistake", "name_te": "తప్పు"}


def test_a_switched_on_category_is_not_deleted(admin, english):
    category_id = admin.post(ADMIN, NEW, format="json").json()["id"]
    response = admin.delete(f"{ADMIN}{category_id}/")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "switch_off_first", "message": "Switch it off first. Only a switched-off item can be edited or deleted.",
    }
    assert Category.objects.filter(pk=category_id).exists()
    assert not AuditLog.objects.filter(action="category.deleted").exists()


def test_the_key_of_a_deleted_category_can_be_used_again(admin):
    deleted = admin.delete(f"{ADMIN}{added_off(admin)}/")
    assert deleted.status_code == 204, deleted.content
    again = admin.post(ADMIN, NEW, format="json")
    assert again.status_code == 201, again.json()


def test_a_switched_off_category_with_posts_is_kept(admin, world, english):
    news = off("news")
    a_post_in(world, news)
    a_post_in(world, news)
    response = admin.delete(f"{ADMIN}{news.id}/")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "category_in_use" and error["count"] == 2
    assert error["message"].startswith("2 posts use this category")
    assert Category.objects.filter(pk=news.pk).exists() and Post.objects.filter(category=news).count() == 2
    assert not AuditLog.objects.filter(action="category.deleted").exists()


def test_a_post_saved_at_the_same_moment_stops_the_delete(admin, world, monkeypatch):
    """The count says 0, but a post arrived before the DELETE: Post.category is PROTECT, so 409."""
    news = off("news")
    a_post_in(world, news)
    real = Post.objects
    calls = {"n": 0}

    class FirstCountSaysNone:
        def filter(self, **kwargs):
            calls["n"] += 1
            return real.none() if calls["n"] == 1 else real.filter(**kwargs)

    monkeypatch.setattr(views, "Post", SimpleNamespace(objects=FirstCountSaysNone()))
    response = admin.delete(f"{ADMIN}{news.id}/")
    assert response.status_code == 409 and response.json()["error"]["code"] == "category_in_use"
    assert Category.objects.filter(pk=news.pk).exists()
    assert not AuditLog.objects.filter(action="category.deleted").exists()  # rolled back with it


def test_the_icon_file_goes_after_the_delete(admin, media_root, django_capture_on_commit_callbacks):
    url = admin.post(ADMIN, {**NEW, "is_active": "false", "icon": icon()}, format="multipart").json()["icon_url"]
    path = media_root / url.split("/media/", 1)[1]
    assert path.exists()
    category = Category.objects.get(key="mistake")
    with django_capture_on_commit_callbacks(execute=True):
        assert admin.delete(f"{ADMIN}{category.id}/").status_code == 204
    assert not path.exists()


def test_an_unknown_category_answers_404(admin):
    assert admin.delete(f"{ADMIN}999999/").status_code == 404


@pytest.mark.parametrize("name", list(OFFICIALS))
def test_only_the_super_admin_may_delete(api, world, name):
    expected = 204 if OFFICIALS[name][0] == Role.SUPER_ADMIN else 403
    category = Category.objects.create(key="mistake", name_te="తప్పు", name_en="Mistake", is_active=False)
    response = api(world.users[name]).delete(f"{ADMIN}{category.id}/")
    assert response.status_code == expected
    assert Category.objects.filter(pk=category.pk).exists() is (expected != 204)


def test_not_logged_in_cannot_delete(world):
    news = off("news")
    assert APIClient().delete(f"{ADMIN}{news.id}/").status_code == 401


# --- Edit --------------------------------------------------------------------------------------------

CHANGE = {"name_te": "తాజా వార్తలు", "name_en": "Latest news", "short_name_te": "వార్తలు", "short_name_en": "News"}


def test_a_switched_off_category_is_edited(admin):
    news = off("news")
    response = admin.patch(f"{ADMIN}{news.id}/", CHANGE, format="json")
    assert response.status_code == 200, response.json()
    news.refresh_from_db()
    assert (news.name_te, news.name_en, news.short_name_te, news.short_name_en) == tuple(CHANGE.values())
    assert AuditLog.objects.get(action="category.updated").data == CHANGE


@pytest.mark.parametrize("change", [{"name_en": "Latest news"}, {"name_te": "తాజా"}, {"short_name_en": "N"}])
def test_a_switched_on_category_is_not_edited(admin, change):
    news = Category.objects.get(key="news")
    before = (news.name_te, news.name_en, news.short_name_en)
    response = admin.patch(f"{ADMIN}{news.id}/", change, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    news.refresh_from_db()
    assert (news.name_te, news.name_en, news.short_name_en) == before
    assert not AuditLog.objects.filter(action="category.updated").exists()


def test_a_switched_on_category_keeps_its_icon(admin, media_root):
    news = Category.objects.get(key="news")
    response = admin.patch(f"{ADMIN}{news.id}/", {"icon": icon()}, format="multipart")
    assert response.status_code == 409 and response.json()["error"]["code"] == "switch_off_first"
    news.refresh_from_db()
    assert not news.icon


def test_the_switch_and_the_order_work_any_time(admin):
    news = Category.objects.get(key="news")
    assert admin.patch(f"{ADMIN}{news.id}/", {"order": 5}, format="json").status_code == 200
    assert admin.patch(f"{ADMIN}{news.id}/", {"is_active": False}, format="json").status_code == 200
    assert admin.patch(f"{ADMIN}{news.id}/", {"is_active": True}, format="json").status_code == 200


def test_unchanged_names_sent_with_the_switch_are_fine(admin):
    """A client sending the whole row back with only the switch changed is not an edit."""
    news = Category.objects.get(key="news")
    body = {"name_en": news.name_en, "name_te": news.name_te, "is_active": False}
    assert admin.patch(f"{ADMIN}{news.id}/", body, format="json").status_code == 200


def test_edit_keeps_the_key(admin, english):
    news = off("news")
    before = news.name_en
    response = admin.patch(f"{ADMIN}{news.id}/", {"key": "other", "name_en": "Other"}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["fields"]["key"] == ["The key of a category cannot be changed."]
    news.refresh_from_db()
    assert (news.key, news.name_en) == ("news", before)


def test_sending_the_same_key_on_edit_is_fine(admin):
    news = Category.objects.get(key="news")
    assert admin.patch(f"{ADMIN}{news.id}/", {"key": "news", "order": 3}, format="json").status_code == 200


@pytest.mark.parametrize("name", [name for name, (role, _) in OFFICIALS.items() if role != Role.SUPER_ADMIN])
def test_no_one_else_may_edit(api, world, name):
    news = off("news")
    response = api(world.users[name]).patch(f"{ADMIN}{news.id}/", {"name_en": "Mine"}, format="json")
    assert response.status_code == 403
    news.refresh_from_db()
    assert news.name_en != "Mine"

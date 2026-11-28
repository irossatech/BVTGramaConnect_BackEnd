"""An uploaded icon for post categories (BVTGC-91; contract section 5, "Categories").

The Super Admin adds a category with an optional `icon` image (multipart), replaces it with a PATCH,
or removes it with `"icon": null`. The icon goes through the same checks as service and bill logos
(grama.logos: JPEG, PNG or WebP, at most SERVICE_LOGO_MAX_UPLOAD_MB, at least SERVICE_LOGO_MIN_PX a
side, stored as WebP at most SERVICE_LOGO_PX). GET /categories/ and the admin list return `icon_url`,
an absolute link or null. A post's nested category does not carry it. Only holders of
posts.manage_categories (the Super Admin) may change categories.
"""

import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.posts.models import Category

pytestmark = pytest.mark.django_db

PUBLIC = "/api/v1/categories/"
ADMIN = "/api/v1/admin/categories/"
NEW = {"key": "health", "name_te": "ఆరోగ్యం", "name_en": "Health"}


def icon(size=(512, 512), fmt="PNG", name="icon.png", mode="RGBA") -> SimpleUploadedFile:
    image = Image.new(mode, size, (31, 138, 59, 128) if mode == "RGBA" else (31, 138, 59))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def on_disk(media_root, url: str) -> Path:
    return Path(media_root) / url.split("/media/", 1)[1]


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def public_row(key):
    return next(row for row in APIClient().get(PUBLIC).json() if row["key"] == key)


def test_a_new_category_with_an_icon(admin, media_root):
    response = admin.post(ADMIN, {**NEW, "icon": icon()}, format="multipart")
    assert response.status_code == 201, response.json()
    url = response.json()["icon_url"]
    assert url.startswith("http://testserver/media/categories/health-") and url.endswith("-256.webp")
    with Image.open(on_disk(media_root, url)) as stored:
        assert stored.format == "WEBP" and max(stored.size) == 256 and stored.mode == "RGBA"
    assert public_row("health")["icon_url"] == url  # also: a form without is_active adds it switched on
    assert AuditLog.objects.get(action="category.created").data == {"key": "health", "icon": True}


def test_a_form_can_still_switch_a_category_off(admin):
    response = admin.post(ADMIN, {**NEW, "is_active": "false", "icon": icon()}, format="multipart")
    assert response.status_code == 201 and response.json()["is_active"] is False


def test_a_new_category_without_an_icon_still_works_as_json(admin):
    response = admin.post(ADMIN, NEW, format="json")
    assert response.status_code == 201 and response.json()["icon_url"] is None
    assert public_row("health")["icon_url"] is None
    assert AuditLog.objects.get(action="category.created").data == {"key": "health", "icon": False}


def test_every_category_carries_icon_url_seeded_ones_null(admin):
    assert all(row["icon_url"] is None for row in APIClient().get(PUBLIC).json())
    assert all(row["icon_url"] is None for row in admin.get(ADMIN).json())


def test_an_icon_is_replaced_kept_and_removed(admin, media_root, django_capture_on_commit_callbacks):
    news = Category.objects.get(key="news")
    Category.objects.filter(pk=news.pk).update(is_active=False)  # the icon changes only while off
    path = f"{ADMIN}{news.id}/"
    with django_capture_on_commit_callbacks(execute=True):
        first = admin.patch(path, {"icon": icon(fmt="JPEG", name="n.jpg", mode="RGB")}, format="multipart").json()["icon_url"]
    with django_capture_on_commit_callbacks(execute=True):
        second = admin.patch(path, {"icon": icon()}, format="multipart").json()["icon_url"]
    assert first != second  # a new name on every change, because phones cache /media/
    assert not on_disk(media_root, first).exists() and on_disk(media_root, second).exists()

    # Changing another field keeps the icon.
    kept = admin.patch(path, {"name_en": "Village news"}, format="json").json()
    assert kept["icon_url"] == second and kept["name_en"] == "Village news"

    with django_capture_on_commit_callbacks(execute=True):
        removed = admin.patch(path, {"icon": None}, format="json").json()
    assert removed["icon_url"] is None and not on_disk(media_root, second).exists()
    Category.objects.filter(key="news").update(is_active=True)  # the public list shows switched-on ones only
    assert public_row("news")["icon_url"] is None
    updates = [entry.data for entry in AuditLog.objects.filter(action="category.updated").order_by("created_at")]
    assert updates == [{"icon": "replaced"}, {"icon": "replaced"}, {"name_en": "Village news"}, {"icon": "removed"}]


@pytest.mark.parametrize(
    "upload, why",
    [
        (SimpleUploadedFile("icon.png", b"just some text, not a picture", content_type="image/png"), "not an image"),
        (SimpleUploadedFile("icon.gif", b"GIF89a" + b"\x00" * 100, content_type="image/gif"), "a GIF"),
        (None, "smaller than SERVICE_LOGO_MIN_PX on one side"),
    ],
)
def test_bad_icons_are_refused_and_nothing_is_saved(admin, media_root, upload, why):
    response = admin.post(ADMIN, {**NEW, "icon": upload or icon(size=(63, 200))}, format="multipart")
    assert response.status_code == 400 and "icon" in response.json()["error"]["fields"], why
    assert not Category.objects.filter(key="health").exists()
    assert not AuditLog.objects.filter(action="category.created").exists()


def test_a_too_large_icon_is_refused_on_change_and_the_old_one_stays(admin, media_root, settings):
    news = Category.objects.get(key="news")
    Category.objects.filter(pk=news.pk).update(is_active=False)  # the icon changes only while off
    url = admin.patch(f"{ADMIN}{news.id}/", {"icon": icon()}, format="multipart").json()["icon_url"]
    settings.SERVICE_LOGO_MAX_UPLOAD_MB = 0
    response = admin.patch(f"{ADMIN}{news.id}/", {"icon": icon(), "name_en": "Changed"}, format="multipart")
    assert response.status_code == 400 and "icon" in response.json()["error"]["fields"]
    news.refresh_from_db()
    Category.objects.filter(key="news").update(is_active=True)  # the public list shows switched-on ones only
    assert news.name_en != "Changed" and public_row("news")["icon_url"] == url


@pytest.mark.parametrize("official", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_may_upload_an_icon(api, world, official):
    news = Category.objects.get(key="news")
    client = api(world.users[official])
    assert client.post(ADMIN, {**NEW, "icon": icon()}, format="multipart").status_code == 403
    assert client.patch(f"{ADMIN}{news.id}/", {"icon": icon()}, format="multipart").status_code == 403
    assert APIClient().post(ADMIN, {**NEW, "icon": icon()}, format="multipart").status_code == 401
    assert not Category.objects.filter(key="health").exists()
    news.refresh_from_db()
    assert not news.icon


def test_a_posts_nested_category_stays_without_icon_url(admin, world, make_post):
    news = Category.objects.get(key="announce")
    admin.patch(f"{ADMIN}{news.id}/", {"icon": icon()}, format="multipart")
    post = make_post("va_kolakaluru", "kolakaluru")
    body = admin.get(f"/api/v1/posts/{post.id}/").json()
    assert body["category"]["key"] == "announce" and "icon_url" not in body["category"]

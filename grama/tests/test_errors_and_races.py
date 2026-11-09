"""Answers in the contract's shape instead of 500s, and no files or rows lost to a race or a stranger
(audit BE_01: BE-COR-001, BE-COR-002, BE-COR-003, BE-SEC-005, BE-SEC-016, BE-CFG-001)."""

from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from rest_framework.test import APIRequestFactory

from apps.accounts import views_users
from apps.accounts.models import User
from apps.accounts.permissions import HasPerm
from apps.accounts.roles import Role
from apps.notifications.models import Device
from apps.posts import services as post_services
from apps.posts.models import Category, PostMedia
from apps.posts.services import PostInput
from apps.service_requests import services as service_services
from apps.service_requests.models import Service


def error(response):
    return response.json()["error"]


# --- BE-COR-001: a value the ORM cannot take is a 400, not a 500 --------------------------------------


@pytest.mark.parametrize("url", [
    "/api/v1/audit/?actor=not-a-uuid",
    "/api/v1/audit/export/?actor=not-a-uuid",
    "/api/v1/posts/?author=not-a-uuid",
])
def test_a_bad_uuid_in_a_filter_answers_400(world, api, url):
    response = api(world.users["super"]).get(url)
    assert response.status_code == 400
    assert error(response)["code"] == "validation_error"


def test_a_bad_media_id_is_not_a_500(world, api):
    post = post_services.create_post(
        world.users["pm_kolakaluru"],
        PostInput(category=Category.objects.get(key="announce"), language="te", title="సభ", body="సభ",
                  target=world.ref("kolakaluru")),
        submit=False,
    )
    response = api(world.users["pm_kolakaluru"]).delete(f"/api/v1/posts/{post.id}/media/abc/")
    assert response.status_code in (400, 404)


# --- BE-COR-002: two creates at once ----------------------------------------------------------------


def test_user_taken_between_the_check_and_the_save_is_a_409(world, api, monkeypatch):
    """The username check passes, then another request saves the same username first."""
    original = views_users.check_new_password

    def someone_else_saves_first(password, user, **kwargs):
        User.objects.create_user(username=user.username, name="Someone else", password="x")
        return original(password, user, **kwargs)

    monkeypatch.setattr(views_users, "check_new_password", someone_else_saves_first)
    response = api(world.users["va_kaza"]).post("/api/v1/users/", {
        "username": "new_member", "name": "New Member", "password": "Grama-New-2026",
        "role": Role.PANCHAYAT_MEMBER, "area": world.area_json("kaza"),
    }, format="json")
    assert response.status_code == 409, response.json()
    assert error(response)["code"] == "duplicate" and "username" in error(response)["fields"]


def test_service_with_the_same_name_at_once_gets_the_name_answer(world, monkeypatch):
    Service.objects.create(key="gas_connection", name_en="Gas Connection")
    checks = iter([None])  # the first name check runs before the other create commits

    def check(name_en, service=None):
        if next(checks, "again") is None:
            return None
        return original_check(name_en, service)

    original_check = service_services._check_name
    monkeypatch.setattr(service_services, "_check_name", check)
    monkeypatch.setattr(service_services, "_new_key", lambda name_en: "gas_connection")
    with pytest.raises(Exception) as caught:
        service_services.create_service(world.users["super"], {"name_en": "Gas Connection"})
    assert getattr(caught.value, "error_code", None) == "validation_error"


# --- BE-COR-003: deleting a draft removes its video too --------------------------------------------


def test_deleting_a_draft_removes_every_file_of_its_media(world, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    author = world.users["pm_kolakaluru"]
    post = post_services.create_post(
        author,
        PostInput(category=Category.objects.get(key="announce"), language="te", title="వీడియో", body="వీడియో",
                  target=world.ref("kolakaluru")),
        submit=False,
    )
    media = PostMedia(post=post, kind="video", width=1, height=1, bytes=1)
    media.video.save("clip.mp4", ContentFile(b"video"), save=False)
    media.image.save("poster.webp", ContentFile(b"poster"), save=False)
    media.thumbnail.save("thumb.webp", ContentFile(b"thumb"), save=False)
    media.save()
    paths = [media.video.path, media.image.path, media.thumbnail.path]
    post_services.delete_post(author, post)
    import os

    assert [os.path.exists(path) for path in paths] == [False, False, False]


# --- BE-SEC-005: a stranger cannot remove an official's device ------------------------------------


def test_anonymous_device_cannot_take_an_officials_push_token(world, api):
    official = Device.objects.create(fcm_token="official-token", platform="android", user=world.users["va_kaza"])
    mine = api().post("/api/v1/devices/", {"fcm_token": "my-token", "platform": "android"}, format="json").json()
    response = api().patch(f"/api/v1/devices/{mine['id']}/", {"fcm_token": "official-token"}, format="json")
    assert response.status_code == 409
    assert error(response)["code"] == "duplicate" and "fcm_token" in error(response)["fields"]
    assert Device.objects.filter(pk=official.pk, user=world.users["va_kaza"]).exists()


def test_the_official_moves_their_own_token(world, api):
    official = world.users["va_kaza"]
    Device.objects.create(fcm_token="phone-token", platform="android", user=official)
    other = api(official).post("/api/v1/devices/", {"fcm_token": "new-install", "platform": "android"}, format="json").json()
    response = api(official).patch(f"/api/v1/devices/{other['id']}/", {"fcm_token": "phone-token"}, format="json")
    assert response.status_code == 200
    assert list(Device.objects.filter(fcm_token="phone-token").values_list("id", flat=True)) == [
        __import__("uuid").UUID(other["id"])
    ]


def test_an_unlinked_old_row_is_still_replaced(world, api):
    Device.objects.create(fcm_token="old-install", platform="android")
    mine = api().post("/api/v1/devices/", {"fcm_token": "fresh", "platform": "android"}, format="json").json()
    assert api().patch(f"/api/v1/devices/{mine['id']}/", {"fcm_token": "old-install"}, format="json").status_code == 200
    assert Device.objects.filter(fcm_token="old-install").count() == 1


# --- BE-SEC-016: an object outside the area is hidden (404), as the conventions say ---------------


def test_object_permission_outside_the_area_is_a_404(world):
    request = APIRequestFactory().get("/")
    request.user = world.users["va_kolakaluru"]
    permission = HasPerm("contacts.manage_contact")
    inside = SimpleNamespace(area=world.ref("kolakaluru"))
    outside = SimpleNamespace(area=world.ref("krishna"))
    assert permission.has_object_permission(request, None, inside) is True
    with pytest.raises(Http404):
        permission.has_object_permission(request, None, outside)


# --- BE-CFG-001: the geography import has a size cap ----------------------------------------------


def test_geography_import_refuses_a_file_over_the_limit(world, api, settings):
    settings.GEO_IMPORT_MAX_BYTES = 10
    upload = SimpleUploadedFile("villages.csv", b"state,district,mandal,village\n" * 3, content_type="text/csv")
    response = api(world.users["super"]).post("/api/v1/geo/villages/import/", {"file": upload}, format="multipart")
    assert response.status_code == 413
    assert error(response)["code"] == "file_too_large"

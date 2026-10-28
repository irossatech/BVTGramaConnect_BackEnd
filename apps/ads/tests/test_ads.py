"""Home banner ads: the Super Admin's ads, and which villages see them. Contract section 17."""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.ads.models import Ad
from apps.audit.models import AuditLog
from apps.geography.models import Village

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/ads/"
PUBLIC = "/api/v1/ads/"


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def banner(size=(2400, 1350), fmt="JPEG", name="banner.jpg", exif=True) -> SimpleUploadedFile:
    image = Image.new("RGB", size, (31, 138, 59))
    buffer = io.BytesIO()
    kwargs = {}
    if exif and fmt == "JPEG":
        data = Image.Exif()
        data[0x8825] = {2: (16.0, 14.0, 17.0)}  # GPS info
        kwargs["exif"] = data
    image.save(buffer, fmt, **kwargs)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def body(world, area="tenali", **changes):
    ref = world.ref(area)
    data = {"title": "Free eye camp at Tenali", "link": "https://example.org/eye-camp",
            "area_level": ref.level, "area_id": ref.id, "starts_on": "", "ends_on": "", "is_active": "true"}
    data.update(changes)
    return data


def create(api, world, area="tenali", image=None, **changes):
    data = body(world, area, **changes)
    data["image"] = image or banner()
    return api(world.users["super"]).post(ADMIN, data, format="multipart")


def shown(world, village="kolakaluru"):
    return [row["title"] for row in APIClient().get(f"{PUBLIC}?village_id={world.areas[village].id}").json()]


def code(response):
    return response.json()["error"]["code"]


# --- Super Admin ---------------------------------------------------------------------------------


def test_the_super_admin_creates_an_ad_stored_as_webp(api, world, media_root):
    response = create(api, world)
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["title"] == "Free eye camp at Tenali" and data["link"] == "https://example.org/eye-camp"
    assert data["area"] == {"level": "MANDAL", "id": world.areas["tenali"].id, "name": "తెనాలి"}
    assert data["starts_on"] is None and data["ends_on"] is None and data["is_active"] is True
    assert data["image"].startswith("http://testserver/media/ads/") and data["image"].endswith("-1600.webp")
    ad = Ad.objects.get()
    assert ad.created_by == world.users["super"] and "banner" not in ad.image.name
    with Image.open(media_root / ad.image.name) as stored:
        assert stored.format == "WEBP" and stored.size == (1600, 900)
        assert not stored.getexif()  # GPS and the rest are gone
    entry = AuditLog.objects.get(action="ad.created")
    assert entry.actor == world.users["super"] and entry.area_level == "MANDAL"


def test_area_names_follow_accept_language(api, world, media_root):
    create(api, world)
    row = api(world.users["super"]).get(ADMIN, HTTP_ACCEPT_LANGUAGE="en").json()["results"][0]
    assert row["area"]["name"] == "Tenali"


def test_the_list_is_paged_newest_first(api, world, media_root):
    for title in ("one", "two", "three"):
        create(api, world, title=title)
    data = api(world.users["super"]).get(f"{ADMIN}?page_size=100").json()
    assert [row["title"] for row in data["results"]] == ["three", "two", "one"] and data["count"] == 3


@pytest.mark.parametrize("name", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_the_super_admin_manages_ads(api, world, media_root, name):
    ad_id = create(api, world).json()["id"]
    client = api(world.users[name])
    responses = [
        client.get(ADMIN),
        client.post(ADMIN, {**body(world), "image": banner()}, format="multipart"),
        client.get(f"{ADMIN}{ad_id}/"),
        client.patch(f"{ADMIN}{ad_id}/", {"is_active": False}, format="json"),
        client.delete(f"{ADMIN}{ad_id}/"),
    ]
    for response in responses:
        assert response.status_code == 403 and code(response) == "permission_denied"
    assert Ad.objects.filter(pk=ad_id, is_active=True).exists()
    assert APIClient().get(ADMIN).status_code == 401


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"title": ""}, "title"),
        ({"title": "x" * 81}, "title"),
        ({"link": "javascript:alert(1)"}, "link"),
        ({"link": "intent://open#Intent;end"}, "link"),
        ({"link": "file:///etc/passwd"}, "link"),
        ({"link": "not a link"}, "link"),
        ({"area_id": 999999}, "area_id"),
        ({"area_level": "COUNTRY"}, "area_level"),
        ({"starts_on": "2026-11-10", "ends_on": "2026-11-01"}, "ends_on"),
    ],
)
def test_bad_fields_are_refused(api, world, media_root, changes, field):
    response = create(api, world, **changes)
    assert response.status_code == 400 and field in response.json()["error"]["fields"], response.json()
    assert not Ad.objects.exists()


@pytest.mark.parametrize(
    "image, why",
    [
        (SimpleUploadedFile("banner.jpg", b"just some text, not a picture", content_type="image/jpeg"), "not an image"),
        (None, "too narrow"),
        (SimpleUploadedFile("banner.gif", b"GIF89a" + b"\x00" * 100, content_type="image/gif"), "a GIF"),
    ],
)
def test_bad_images_are_refused(api, world, media_root, image, why):
    image = image or banner(size=(799, 450))
    response = create(api, world, image=image)
    assert response.status_code == 400 and "image" in response.json()["error"]["fields"], why
    assert not Ad.objects.exists()


def test_a_too_large_image_is_refused(api, world, media_root, settings):
    settings.ADS_MAX_UPLOAD_MB = 0
    response = create(api, world)
    assert response.status_code == 400 and "image" in response.json()["error"]["fields"]


def test_an_image_is_required_on_create(api, world, media_root):
    response = api(world.users["super"]).post(ADMIN, body(world), format="multipart")
    assert response.status_code == 400 and "image" in response.json()["error"]["fields"]


def test_png_and_webp_are_accepted_and_small_ones_are_not_enlarged(api, world, media_root):
    assert create(api, world, image=banner(fmt="PNG", name="b.png")).status_code == 201
    response = create(api, world, image=banner(size=(1000, 560), fmt="WEBP", name="b.webp"))
    assert response.status_code == 201
    with Image.open(media_root / Ad.objects.get(pk=response.json()["id"]).image.name) as stored:
        assert stored.size == (1000, 560)


# --- Changing and deleting -----------------------------------------------------------------------


def test_a_json_patch_switches_the_ad_off(api, world, media_root):
    ad_id = create(api, world).json()["id"]
    assert shown(world) == ["Free eye camp at Tenali"]
    response = api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", {"is_active": False}, format="json")
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert shown(world) == []
    assert AuditLog.objects.get(action="ad.updated").data == {"changed": ["is_active"]}


def test_a_json_patch_changes_the_details_and_clears_a_date(api, world, media_root):
    ad_id = create(api, world, ends_on="2030-01-01").json()["id"]
    village = world.ref("kolakaluru")
    changes = {"title": "New title", "link": "", "area_level": "VILLAGE", "area_id": village.id,
               "starts_on": "2026-10-28", "ends_on": None, "is_active": True}
    data = api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", changes, format="json").json()
    assert (data["title"], data["link"], data["starts_on"], data["ends_on"]) == ("New title", "", "2026-10-28", None)
    assert data["area"]["level"] == "VILLAGE"


def test_a_multipart_patch_replaces_the_image_and_its_file(api, world, media_root, django_capture_on_commit_callbacks):
    ad_id = create(api, world).json()["id"]
    old = Ad.objects.get().image.name
    with django_capture_on_commit_callbacks(execute=True):  # the old file goes after the commit
        response = api(world.users["super"]).patch(
            f"{ADMIN}{ad_id}/", {"title": "With a new picture", "image": banner(size=(1200, 675))}, format="multipart"
        )
    assert response.status_code == 200
    new = Ad.objects.get().image.name
    assert new != old and response.json()["image"].endswith(new.split("/")[-1])
    assert not (media_root / old).exists() and (media_root / new).exists()
    assert AuditLog.objects.get(action="ad.updated").data == {"changed": ["title", "image"]}


def test_an_empty_patch_changes_nothing(api, world, media_root):
    ad_id = create(api, world).json()["id"]
    assert api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", {}, format="json").status_code == 200
    assert not AuditLog.objects.filter(action="ad.updated").exists()


def test_patch_checks_dates_against_the_stored_ones(api, world, media_root):
    ad_id = create(api, world, starts_on="2026-11-10").json()["id"]
    response = api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", {"ends_on": "2026-11-01"}, format="json")
    assert response.status_code == 400 and "ends_on" in response.json()["error"]["fields"]


def test_delete_removes_the_ad_and_its_file(api, world, media_root, django_capture_on_commit_callbacks):
    ad_id = create(api, world).json()["id"]
    name = Ad.objects.get().image.name
    with django_capture_on_commit_callbacks(execute=True):  # the file goes after the commit
        assert api(world.users["super"]).delete(f"{ADMIN}{ad_id}/").status_code == 204
    assert not Ad.objects.exists() and not (media_root / name).exists()
    assert AuditLog.objects.get(action="ad.deleted").data == {"ad_id": ad_id}
    assert api(world.users["super"]).delete(f"{ADMIN}{ad_id}/").status_code == 404


# --- Citizens --------------------------------------------------------------------------------------


@pytest.mark.parametrize("area", ["kolakaluru", "tenali", "guntur", "ap"])
def test_a_village_sees_ads_for_itself_and_every_area_above(api, world, media_root, area):
    create(api, world, area=area, title=f"for {area}")
    assert shown(world, "kolakaluru") == [f"for {area}"]


@pytest.mark.parametrize("area", ["nandivelugu", "mangalagiri", "krishna", "kaza"])
def test_other_areas_ads_are_not_shown(api, world, media_root, area):
    create(api, world, area=area)
    assert shown(world, "kolakaluru") == []


def test_dates_and_the_switch_decide_what_is_shown(api, world, media_root):
    today = timezone.localdate()
    create(api, world, title="off", is_active="false")
    create(api, world, title="tomorrow", starts_on=(today + timedelta(days=1)).isoformat())
    create(api, world, title="ended yesterday", ends_on=(today - timedelta(days=1)).isoformat())
    create(api, world, title="ends today", ends_on=today.isoformat())
    create(api, world, title="started today", starts_on=today.isoformat())
    create(api, world, title="no dates")
    assert shown(world) == ["no dates", "started today", "ends today"]


def test_the_public_shape_has_only_four_fields(api, world, media_root):
    create(api, world)
    response = APIClient().get(f"{PUBLIC}?village_id={world.areas['kolakaluru'].id}")
    assert response.status_code == 200 and "max-age=300" in response["Cache-Control"]
    (row,) = response.json()
    assert set(row) == {"id", "title", "image", "link"}
    assert row["image"].startswith("http://testserver/media/ads/")


def test_at_most_ten_ads_newest_first(api, world, media_root):
    for index in range(12):
        create(api, world, title=f"ad {index:02d}", image=banner(size=(800, 450)))
    assert shown(world) == [f"ad {index:02d}" for index in range(11, 1, -1)]


def test_public_list_needs_an_active_village(world):
    assert APIClient().get(PUBLIC).status_code == 400
    assert APIClient().get(f"{PUBLIC}?village_id=abc").status_code == 400
    assert APIClient().get(f"{PUBLIC}?village_id=999999").status_code == 404
    Village.objects.filter(pk=world.areas["kolakaluru"].pk).update(is_active=False)
    assert APIClient().get(f"{PUBLIC}?village_id={world.areas['kolakaluru'].id}").status_code == 404


def test_the_public_list_ignores_any_login(api, world, media_root):
    create(api, world)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")
    assert client.get(f"{PUBLIC}?village_id={world.areas['kolakaluru'].id}").status_code == 200

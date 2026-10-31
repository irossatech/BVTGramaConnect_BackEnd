"""Video banners: an ad with a video, its poster as `image`, and the swap between picture and video.

Contract section 17. ffprobe and ffmpeg are faked like the post video tests: no binary runs.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.ads.models import Ad
from apps.audit.models import AuditLog
from apps.posts import media as media_service

from .test_ads import ADMIN, PUBLIC, banner, body, code, create

pytestmark = pytest.mark.django_db


def mp4(name="clip.mp4", size=2048) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (size - 12), content_type="video/mp4")


def frame(size=(1920, 1080)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def ffmpeg(monkeypatch):
    """Pretend ffprobe and ffmpeg are installed: the video is 10 s long, the frame 1920 x 1080."""
    calls = {"length": 10.0, "frame": (1920, 1080)}
    monkeypatch.setattr(media_service, "probe_duration", lambda path: calls["length"])
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: frame(calls["frame"]))
    return calls


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media_service, "probe_duration", lambda path: None)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: None)


def create_video(api, world, video=None, **changes):
    data = body(world, **changes)
    data["video"] = video or mp4()
    return api(world.users["super"]).post(ADMIN, data, format="multipart")


def patch(api, world, ad_id, **files):
    return api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", files, format="multipart")


# --- Create ----------------------------------------------------------------------------------------


def test_a_video_ad_keeps_the_video_and_its_poster_as_the_image(api, world, media_root, ffmpeg):
    response = create_video(api, world)
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["duration_s"] == 10
    assert data["video"].startswith("http://testserver/media/ads/") and data["video"].endswith(".mp4")
    assert data["image"].endswith("-1600.webp")
    ad = Ad.objects.get()
    assert "clip" not in ad.video.name and ad.video.name.startswith(f"ads/{ad.pk}-")
    assert (media_root / ad.video.name).read_bytes()[4:8] == b"ftyp"
    with Image.open(media_root / ad.image.name) as poster:
        assert poster.format == "WEBP" and poster.size == (1600, 900)
    entry = AuditLog.objects.get(action="ad.created")
    assert entry.data == {"kind": "video"}


def test_a_picture_ad_says_so(api, world, media_root):
    data = create(api, world).json()
    assert data["video"] == "" and data["duration_s"] is None
    assert AuditLog.objects.get(action="ad.created").data == {"kind": "image"}


def test_a_narrow_portrait_frame_is_a_fine_poster(api, world, media_root, ffmpeg):
    ffmpeg["frame"] = (480, 854)  # narrower than ADS_MIN_WIDTH_PX, which only pictures must meet
    response = create_video(api, world)
    assert response.status_code == 201, response.json()
    with Image.open(media_root / Ad.objects.get().image.name) as poster:
        assert poster.size == (480, 854)


def test_neither_file_asks_for_an_image_or_video(api, world, media_root):
    response = api(world.users["super"]).post(ADMIN, body(world), format="multipart")
    assert response.status_code == 400 and "image" in response.json()["error"]["fields"]


def test_both_files_are_refused_on_video(api, world, media_root, ffmpeg):
    response = create_video(api, world, image=banner())
    assert response.status_code == 400 and "video" in response.json()["error"]["fields"]
    assert not Ad.objects.exists()


@pytest.mark.parametrize(
    "video",
    [
        SimpleUploadedFile("clip.mp4", b"just some text, not a video" * 10, content_type="video/mp4"),
        SimpleUploadedFile("clip.avi", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100, content_type="video/x-msvideo"),
    ],
    ids=["text-named-mp4", "avi"],
)
def test_not_an_mp4_or_mov(api, world, media_root, ffmpeg, video):
    response = create_video(api, world, video=video)
    assert response.status_code == 415 and code(response) == "unsupported_media"
    assert not Ad.objects.exists()


@pytest.mark.parametrize("length, expected", [(60.4, 201), (61.0, 400)])
def test_at_most_sixty_seconds(api, world, media_root, ffmpeg, length, expected):
    ffmpeg["length"] = length
    response = create_video(api, world)
    assert response.status_code == expected
    if expected == 400:
        assert code(response) == "video_too_long" and not Ad.objects.exists()


def test_the_banner_limits_are_their_own(api, world, media_root, ffmpeg, settings):
    settings.ADS_VIDEO_MAX_SECONDS = 20
    settings.ADS_VIDEO_MAX_MB = 1
    ffmpeg["length"] = 30.0
    assert code(create_video(api, world)) == "video_too_long"
    ffmpeg["length"] = 10.0
    too_big = create_video(api, world, video=mp4(size=1024 * 1024 + 1))
    assert too_big.status_code == 413 and code(too_big) == "file_too_large"
    # Post videos keep their own limits (MAX_VIDEO_UPLOAD_MB, VIDEO_MAX_SECONDS).
    ffmpeg["length"] = 30.0
    prepared = media_service.prepare_video(mp4(size=1024 * 1024 + 1))
    assert prepared.duration == 30.0 and prepared.poster is not None


def test_without_ffmpeg_a_plain_poster_and_the_phones_length(api, world, media_root, no_ffmpeg):
    response = create_video(api, world, duration_s="18")
    assert response.status_code == 201, response.json()
    assert response.json()["duration_s"] == 18
    with Image.open(media_root / Ad.objects.get().image.name) as poster:
        assert poster.size == (1280, 720)
    unknown = create_video(api, world).json()
    assert unknown["duration_s"] is None and unknown["video"]


def test_without_ffprobe_the_phones_length_is_still_checked(api, world, media_root, no_ffmpeg):
    response = create_video(api, world, duration_s="75")
    assert response.status_code == 400 and code(response) == "video_too_long"


# --- Change and delete -------------------------------------------------------------------------------


def test_a_video_turns_a_picture_ad_into_a_video_ad(api, world, media_root, ffmpeg, django_capture_on_commit_callbacks):
    ad_id = create(api, world).json()["id"]
    old_image = Ad.objects.get().image.name
    with django_capture_on_commit_callbacks(execute=True):
        response = patch(api, world, ad_id, video=mp4(), duration_s="9")
    assert response.status_code == 200, response.json()
    ad = Ad.objects.get()
    assert response.json()["video"].endswith(ad.video.name.split("/")[-1]) and ad.duration_s == 10  # measured wins
    assert not (media_root / old_image).exists() and (media_root / ad.image.name).exists()
    entry = AuditLog.objects.get(action="ad.updated")
    assert set(entry.data["changed"]) == {"image", "video"}


def test_a_picture_turns_a_video_ad_back(api, world, media_root, ffmpeg, django_capture_on_commit_callbacks):
    ad_id = create_video(api, world).json()["id"]
    old = Ad.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        response = patch(api, world, ad_id, image=banner())
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["video"] == "" and data["duration_s"] is None
    ad = Ad.objects.get()
    assert not ad.video and ad.duration_s is None
    assert not (media_root / old.video.name).exists() and not (media_root / old.image.name).exists()
    assert (media_root / ad.image.name).exists()
    assert set(AuditLog.objects.get(action="ad.updated").data["changed"]) == {"image", "video"}


def test_a_new_video_replaces_the_old_one_under_a_new_name(api, world, media_root, ffmpeg, django_capture_on_commit_callbacks):
    ad_id = create_video(api, world).json()["id"]
    old = Ad.objects.get()
    ffmpeg["length"] = 25.0
    with django_capture_on_commit_callbacks(execute=True):
        response = patch(api, world, ad_id, video=mp4(name="other.MOV"))
    assert response.status_code == 200, response.json()
    ad = Ad.objects.get()
    assert ad.video.name.endswith(".mov") and ad.duration_s == 25
    assert ad.video.name != old.video.name and ad.image.name != old.image.name
    assert not (media_root / old.video.name).exists() and not (media_root / old.image.name).exists()


def test_details_alone_keep_the_video(api, world, media_root, ffmpeg):
    ad_id = create_video(api, world).json()["id"]
    response = api(world.users["super"]).patch(f"{ADMIN}{ad_id}/", {"title": "New title"}, format="json")
    assert response.status_code == 200 and response.json()["video"] and response.json()["duration_s"] == 10


def test_delete_removes_the_video_and_its_poster(api, world, media_root, ffmpeg, django_capture_on_commit_callbacks):
    ad_id = create_video(api, world).json()["id"]
    ad = Ad.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        assert api(world.users["super"]).delete(f"{ADMIN}{ad_id}/").status_code == 204
    assert not (media_root / ad.video.name).exists() and not (media_root / ad.image.name).exists()


# --- Citizens ----------------------------------------------------------------------------------------


def test_citizens_get_the_video_and_its_length_and_nothing_internal(api, world, media_root, ffmpeg):
    create_video(api, world, title="video ad")
    create(api, world, title="picture ad")
    rows = APIClient().get(f"{PUBLIC}?village_id={world.areas['kolakaluru'].id}").json()
    by_title = {row["title"]: row for row in rows}
    assert all(set(row) == {"id", "title", "image", "video", "duration_s", "link"} for row in rows)
    assert by_title["video ad"]["video"].endswith(".mp4") and by_title["video ad"]["duration_s"] == 10
    assert by_title["video ad"]["image"].endswith("-1600.webp")
    assert by_title["picture ad"]["video"] == "" and by_title["picture ad"]["duration_s"] is None

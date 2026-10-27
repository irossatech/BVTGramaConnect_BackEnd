"""Citizen posts with videos: any number, mixed with photos, no poster needed. Contract section 15."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.posts import media as media_service
from apps.posts.models import MediaKind, Post, PostMedia

from .conftest import POSTS, photo

pytestmark = pytest.mark.django_db


def mp4(name="clip.mp4", size=2048) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (size - 12), content_type="video/mp4")


def png_frame() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1920, 1080), (200, 80, 40)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def ffmpeg(monkeypatch):
    """The server has ffprobe and ffmpeg: every video is 42 s long and gets a real frame as poster."""
    monkeypatch.setattr(media_service, "probe_duration", lambda path: 42.0)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: png_frame())


@pytest.fixture
def no_ffmpeg(monkeypatch):
    """The qa server today: no ffprobe, no ffmpeg."""
    monkeypatch.setattr(media_service, "probe_duration", lambda path: None)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: None)


def send(citizen, photos=(), videos=()):
    data = {"category": "news", "language": "te", "title": "వరద నీరు", "body": "రోడ్డు మీద నీరు నిలిచింది."}
    if photos:
        data["photos"] = list(photos)
    if videos:
        data["videos"] = list(videos)
    return citizen.client.post(POSTS, data, format="multipart")


def code(response):
    return response.json()["error"]["code"]


def test_photos_and_videos_together_any_number(world, phone, media_root, ffmpeg):
    response = send(phone(), photos=[photo(), photo((800, 600))], videos=[mp4(), mp4("b.mov"), mp4("c.mp4")])
    assert response.status_code == 201, response.json()
    data = response.json()
    assert len(data["photos"]) == 2 and len(data["videos"]) == 3
    video = data["videos"][0]
    assert video["url"].endswith(".mp4") and video["poster_url"].endswith("-poster-1280.webp")
    assert video["thumb_url"].endswith("-poster-320.webp") and video["duration_s"] == 42
    assert data["videos"][1]["url"].endswith(".mov")
    kinds = list(PostMedia.objects.order_by("order").values_list("kind", flat=True))
    assert kinds == ["image", "image", "video", "video", "video"]  # photos first, then videos, in the order sent
    assert all(m.medium for m in PostMedia.objects.all())


def test_a_file_saved_to_a_temporary_file_is_accepted(world, phone, media_root, ffmpeg, settings):
    """Phone videos are over FILE_UPLOAD_MAX_MEMORY_SIZE (2.5 MB), so Django keeps them in a temporary
    file. Copying request.data deep-copied that open file and failed: "cannot pickle 'BufferedRandom'"."""
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 0  # every upload becomes a TemporaryUploadedFile
    response = send(phone(), photos=[photo()], videos=[mp4()])
    assert response.status_code == 201, response.content[:300]
    data = response.json()
    assert len(data["photos"]) == 1 and len(data["videos"]) == 1


def test_a_video_only_post(world, phone, media_root, ffmpeg):
    response = send(phone(), videos=[mp4()])
    assert response.status_code == 201
    assert response.json()["photos"] == [] and len(response.json()["videos"]) == 1


def test_without_ffmpeg_a_video_still_goes_through(world, phone, media_root, no_ffmpeg):
    """Unlike officials, a citizen never has to send a length or a poster."""
    response = send(phone(), videos=[mp4()])
    assert response.status_code == 201, response.json()
    video = response.json()["videos"][0]
    assert video["duration_s"] is None
    stored = PostMedia.objects.get()
    with Image.open(media_root / stored.image.name) as poster:
        assert poster.format == "WEBP" and poster.size == (1280, 720)  # the plain placeholder


def test_reviewers_see_and_publish_the_videos(api, world, phone, media_root, ffmpeg):
    post_id = send(phone(), photos=[photo()], videos=[mp4(), mp4()]).json()["id"]
    reviewer = api(world.users["va_kolakaluru"])
    media = reviewer.get(f"/api/v1/posts/{post_id}/").json()["media"]
    assert [m["kind"] for m in media] == ["image", "video", "video"]
    assert reviewer.post(f"/api/v1/posts/{post_id}/review/", {"action": "publish"}, format="json").status_code == 200
    detail = APIClient().get(f"/api/v1/feed/posts/{post_id}/").json()
    assert [m["kind"] for m in detail["media"]] == ["image", "video", "video"]
    row = APIClient().get(f"/api/v1/feed/?village_id={world.areas['kolakaluru'].id}").json()["results"][0]
    assert row["media_count"] == 3 and row["video"]["url"].endswith(".mp4")
    assert row["image_url"].endswith("-640.webp")  # the photo, for the big card


def test_a_bad_video_leaves_no_post_behind(world, phone, media_root, ffmpeg):
    fake = SimpleUploadedFile("clip.mp4", b"RIFF....AVI LIST" + b"\x00" * 100, content_type="video/mp4")
    response = send(phone(), photos=[photo()], videos=[mp4(), fake])
    assert response.status_code == 415 and code(response) == "unsupported_media"
    assert not Post.objects.exists() and not PostMedia.objects.exists()


def test_each_video_keeps_the_size_and_length_limits(world, phone, media_root, ffmpeg, settings, monkeypatch):
    settings.MAX_VIDEO_UPLOAD_MB = 1
    big = send(phone(), videos=[mp4(size=1024 * 1024 + 1)])
    assert big.status_code == 413 and code(big) == "file_too_large"
    settings.MAX_VIDEO_UPLOAD_MB = 50
    monkeypatch.setattr(media_service, "probe_duration", lambda path: 75.0)
    long = send(phone(ip="10.3.3.3"), videos=[mp4()])
    assert long.status_code == 400 and code(long) == "video_too_long"
    assert not Post.objects.exists()


def test_videos_follow_the_video_switch(world, phone, media_root, ffmpeg, settings):
    settings.VIDEO_POSTS_ENABLED = False
    response = send(phone(), videos=[mp4()])
    assert response.status_code == 403 and code(response) == "permission_denied"
    assert send(phone(ip="10.4.4.4"), photos=[photo()]).status_code == 201  # photos still work


def test_officials_still_need_a_poster_without_ffmpeg(api, world, media_root, no_ffmpeg):
    """The officials' upload is unchanged: no ffmpeg means the phone sends the length and a poster."""
    body = {"category": "announce", "title": "T", "body": "B", "target": world.area_json("kolakaluru"), "submit": False}
    post_id = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json").json()["id"]
    response = api(world.users["pm_kolakaluru"]).post(
        f"/api/v1/posts/{post_id}/media/", {"file": mp4(), "kind": "video", "duration_s": "20"}, format="multipart"
    )
    assert response.status_code == 400 and "poster" in response.json()["error"]["fields"]
    assert not PostMedia.objects.filter(kind=MediaKind.VIDEO).exists()

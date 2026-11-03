"""Security gap G7: uploaded videos are stored without their metadata (GPS place, phone, dates).

The fakes stand in for ffmpeg; the last test runs the real ffmpeg and ffprobe where they are
installed (the server), and is skipped elsewhere.
"""

import io
import json
import os
import shutil
import subprocess

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.ads.models import Ad
from apps.posts import media as media_service
from apps.posts.models import PostMedia

pytestmark = pytest.mark.django_db

POSTS = "/api/v1/posts/"
UPLOADED = b"\x00\x00\x00\x18ftypmp42" + b"GPS+16.2-80.6" + b"\x00" * 500
STRIPPED = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300


def mp4(data=UPLOADED, name="clip.mp4") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="video/mp4")


def frame() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), (200, 80, 40)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """ffprobe and the poster faked; ffmpeg's copy faked at subprocess level, so strip_metadata itself runs."""
    calls = {"returncode": 0, "output": STRIPPED}

    def run(args, **kwargs):
        calls["args"] = args
        with open(args[-1], "wb") as out:
            out.write(calls["output"])
        return subprocess.CompletedProcess(args, calls["returncode"], b"", b"")

    monkeypatch.setattr(media_service, "probe_duration", lambda path: 12.0)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: frame())
    monkeypatch.setattr(media_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(media_service.subprocess, "run", run)
    return calls


# --- strip_metadata ----------------------------------------------------------------------------


def test_the_copy_drops_metadata_without_re_encoding(fake_ffmpeg, tmp_path):
    source = tmp_path / "in.mp4"
    source.write_bytes(UPLOADED)
    stripped = media_service.strip_metadata(str(source), "mp4")
    assert stripped.read() == STRIPPED and stripped.size == len(STRIPPED)
    args = fake_ffmpeg["args"]
    assert args[args.index("-map_metadata") + 1] == "-1" and args[args.index("-c") + 1] == "copy"
    assert "-map_chapters" in args and "+faststart" in args and args[args.index("-f") + 1] == "mp4"
    assert "0:d" not in args  # only video and audio are mapped: data streams (GPS tracks) stay behind
    assert not os.path.exists(args[-1])  # the named temporary file is gone


def test_without_ffmpeg_nothing_is_stripped(monkeypatch, tmp_path):
    monkeypatch.setattr(media_service.shutil, "which", lambda name: None)
    assert media_service.strip_metadata(str(tmp_path / "in.mp4"), "mp4") is None


@pytest.mark.parametrize("returncode, output", [(1, b""), (0, b"")], ids=["ffmpeg-failed", "empty-output"])
def test_a_video_ffmpeg_cannot_copy_is_refused(fake_ffmpeg, tmp_path, returncode, output):
    fake_ffmpeg.update(returncode=returncode, output=output)
    source = tmp_path / "in.mp4"
    source.write_bytes(UPLOADED)
    with pytest.raises(media_service.ApiError) as error:
        media_service.strip_metadata(str(source), "mp4")
    assert error.value.error_code == "unsupported_media"


# --- What gets stored --------------------------------------------------------------------------


def test_a_post_video_is_stored_stripped(api, world, make_post, media_root, fake_ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = api(world.users["pm_kolakaluru"]).post(
        f"{POSTS}{post.id}/media/", {"file": mp4(), "kind": "video"}, format="multipart"
    )
    assert response.status_code == 201, response.json()
    media = PostMedia.objects.get()
    assert (media_root / media.video.name).read_bytes() == STRIPPED
    assert response.json()["bytes"] == len(STRIPPED) == media.bytes


def test_a_mov_stays_a_mov(api, world, make_post, media_root, fake_ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = api(world.users["pm_kolakaluru"]).post(
        f"{POSTS}{post.id}/media/", {"file": mp4(name="clip.MOV"), "kind": "video"}, format="multipart"
    )
    assert response.status_code == 201 and response.json()["url"].endswith(".mov")
    assert fake_ffmpeg["args"][fake_ffmpeg["args"].index("-f") + 1] == "mov"


def test_a_banner_video_is_stored_stripped(api, world, media_root, fake_ffmpeg):
    ref = world.ref("tenali")
    data = {"title": "Eye camp", "area_level": ref.level, "area_id": ref.id, "video": mp4()}
    response = api(world.users["super"]).post("/api/v1/admin/ads/", data, format="multipart")
    assert response.status_code == 201, response.json()
    assert (media_root / Ad.objects.get().video.name).read_bytes() == STRIPPED


def test_a_refused_copy_stores_nothing(api, world, make_post, media_root, fake_ffmpeg):
    fake_ffmpeg["returncode"] = 1
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = api(world.users["pm_kolakaluru"]).post(
        f"{POSTS}{post.id}/media/", {"file": mp4(), "kind": "video"}, format="multipart"
    )
    assert response.status_code == 415 and not PostMedia.objects.exists()


# --- The real ffmpeg (server only) ---------------------------------------------------------------


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_real_ffmpeg_removes_the_place_a_phone_wrote(tmp_path):
    source = tmp_path / "phone.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
         "-c:v", "mpeg4", "-metadata", "location=+16.2400+080.6400/", "-metadata", "title=home",
         str(source)],
        check=True, timeout=60,
    )

    def tags(path):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(probe.stdout).get("format", {}).get("tags", {})

    assert "location" in tags(source)
    stripped = media_service.strip_metadata(str(source), "mp4")
    out = tmp_path / "stored.mp4"
    out.write_bytes(stripped.read())
    remaining = tags(out)
    assert "location" not in remaining and "title" not in remaining
    assert media_service.probe_duration(str(out)) == pytest.approx(1.0, abs=0.2)

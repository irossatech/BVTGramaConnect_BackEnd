"""A citizen post shares one time budget for all its photos and videos (audit BE-PERF-003).
Any number of files stays allowed (lead's decision); only the time spent checking them is capped."""

import pytest

from apps.posts import media as media_service
from apps.posts.models import Post, PostMedia

from .conftest import POSTS, photo
from .test_videos import mp4, png_frame

pytestmark = pytest.mark.django_db


@pytest.fixture
def ffmpeg(monkeypatch):
    monkeypatch.setattr(media_service, "probe_duration", lambda path: 42.0)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: png_frame())
    monkeypatch.setattr(media_service, "strip_metadata", lambda path, container: None)


def send(phone, photos=(), videos=()):
    data = {"category": "news", "language": "te", "title": "వరద నీరు", "body": "రోడ్డు మీద నీరు నిలిచింది."}
    if photos:
        data["photos"] = list(photos)
    if videos:
        data["videos"] = list(videos)
    return phone.client.post(POSTS, data, format="multipart")


def test_many_files_within_the_budget_are_all_kept(world, phone, media_root, ffmpeg):
    response = send(phone(), photos=[photo(), photo()], videos=[mp4(f"v{i}.mp4") for i in range(6)])
    assert response.status_code == 201, response.json()
    assert PostMedia.objects.count() == 8


def test_a_post_over_the_budget_is_refused_and_nothing_is_stored(world, phone, media_root, ffmpeg, monkeypatch):
    citizen = phone()
    clock = {"now": 1000.0}

    def slow_probe(path):
        clock["now"] += 40  # each video takes 40 s to check
        return 42.0

    monkeypatch.setattr(media_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(media_service, "probe_duration", slow_probe)
    response = send(citizen, photos=[photo()], videos=[mp4(f"v{i}.mp4") for i in range(4)])
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error" and "videos" in error["fields"]
    assert not Post.objects.filter(citizen__isnull=False).exists() and not PostMedia.objects.exists()

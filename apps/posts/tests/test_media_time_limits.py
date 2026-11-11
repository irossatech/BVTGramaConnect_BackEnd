"""Time limits on checking uploads (audit BE-PERF-003): a slow ffprobe/ffmpeg step answers 415 instead
of crashing, and one request's photos and videos share MEDIA_PROCESSING_BUDGET_S."""

import subprocess

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.posts import media
from grama.exceptions import ApiError


def mp4(name="clip.mp4", size=2048) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (size - 12), content_type="video/mp4")


@pytest.fixture
def tools(monkeypatch):
    """ffprobe and ffmpeg are installed; `tools.calls` records each run and its timeout."""

    class Tools:
        calls: list[tuple[str, float]] = []
        slow: set[str] = set()

    def fake_run(command, timeout, check, **kwargs):
        name = command[0]
        Tools.calls.append((name, timeout))
        if name in Tools.slow:
            raise subprocess.TimeoutExpired(command, timeout)
        stdout = "42.0" if name == "ffprobe" else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    Tools.calls, Tools.slow = [], set()
    monkeypatch.setattr(media.shutil, "which", lambda name: name)
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return Tools


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves by hand."""

    class Clock:
        now = 1000.0

    monkeypatch.setattr(media.time, "monotonic", lambda: Clock.now)
    return Clock


def test_each_step_has_its_own_limit(tools, settings):
    settings.VIDEO_PROBE_TIMEOUT_S, settings.VIDEO_POSTER_TIMEOUT_S = 15, 20
    assert media.probe_duration("clip.mp4") == 42.0
    media.extract_poster("clip.mp4", 1.0)
    assert tools.calls == [("ffprobe", 15), ("ffmpeg", 20)]


@pytest.mark.parametrize("step", ["probe", "poster", "strip"])
def test_a_step_that_runs_out_of_time_answers_415_not_a_crash(tools, step):
    run = {
        "probe": lambda: media.probe_duration("clip.mp4"),
        "poster": lambda: media.extract_poster("clip.mp4", 1.0),
        "strip": lambda: media.strip_metadata("clip.mp4", "mp4"),
    }[step]
    tools.slow = {"ffprobe" if step == "probe" else "ffmpeg"}
    with pytest.raises(ApiError) as caught:
        run()
    assert caught.value.status_code == 415 and caught.value.error_code == "unsupported_media"


def test_a_video_whose_check_runs_out_of_time_is_refused_with_415(tools):
    tools.slow = {"ffmpeg"}
    with pytest.raises(ApiError) as caught:
        media.prepare_video(mp4())
    assert caught.value.status_code == 415


def test_a_step_gets_only_what_is_left_of_the_budget(tools, clock, settings):
    settings.MEDIA_PROCESSING_BUDGET_S = 90
    with media.media_budget("videos"):
        clock.now += 80  # 10 s left: less than ffprobe's own 15 s
        media.probe_duration("clip.mp4")
    assert tools.calls == [("ffprobe", 10)]


def test_running_out_of_the_budget_answers_400_on_the_named_field(tools, clock):
    with media.media_budget("videos"):
        clock.now += 85  # 5 s left, then ffprobe uses them up
        tools.slow = {"ffprobe"}
        with pytest.raises(ApiError) as caught:
            media.probe_duration("clip.mp4")
    assert caught.value.status_code == 400 and caught.value.error_code == "validation_error"
    assert list(caught.value.fields) == ["videos"]


def test_a_spent_budget_refuses_the_next_file_before_any_work(tools, clock):
    with media.media_budget("photos"):
        clock.now += 91
        with pytest.raises(ApiError) as caught:
            media.process_image(SimpleUploadedFile("p.jpg", b"not read"))
    assert caught.value.status_code == 400 and list(caught.value.fields) == ["photos"]
    assert tools.calls == []


def test_nested_budgets_share_the_outer_one():
    with media.media_budget("videos") as outer, media.media_budget("file") as inner:
        assert inner is outer and inner.field == "videos"
    with media.media_budget() as alone:
        assert alone.field == "file"


def test_a_banner_ad_names_its_own_video_field(tools, clock, settings):
    from apps.ads import services as ads

    settings.MEDIA_PROCESSING_BUDGET_S = 90
    real_run = media.subprocess.run

    def slow_first_step(command, timeout, check, **kwargs):
        clock.now += 95  # the first step uses up the whole budget
        return real_run(command, timeout=timeout, check=check, **kwargs)

    media.subprocess.run = slow_first_step
    with pytest.raises(ApiError) as caught:
        ads.prepare(mp4())
    assert caught.value.status_code == 400 and list(caught.value.fields) == ["video"]


def test_outside_a_request_budget_only_the_step_limit_applies(tools, settings):
    settings.VIDEO_STRIP_TIMEOUT_S = 45
    tools.slow = set()
    media.check_budget()  # nothing to refuse
    with pytest.raises(ApiError):
        media.strip_metadata("clip.mp4", "mp4")  # the fake ffmpeg writes no output file: 415
    assert tools.calls == [("ffmpeg", 45)]

"""Owner decisions on posts: event date (Q7), urgent (Q19), video (Q8), "Visible to" (Q5)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from PIL import Image

from apps.audit.models import AuditLog
from apps.posts import media as media_service
from apps.posts.models import MediaKind, Post, PostMedia, PostStatus

pytestmark = pytest.mark.django_db

POSTS = "/api/v1/posts/"


def code(response):
    return response.json()["error"]["code"]


def body(world, target="kolakaluru", **extra):
    data = {"category": "announce", "language": "te", "title": "గ్రామ సభ", "body": "అక్టోబర్ 20న.", "target": world.area_json(target)}
    data.update(extra)
    return data


# --- Event date (Q7) ----------------------------------------------------------------------


def test_post_with_an_event_date(api, world):
    response = api(world.users["va_kolakaluru"]).post(
        POSTS, body(world, event={"kind": "meeting", "start_date": "2026-11-05"}), format="json"
    )
    assert response.status_code == 201
    assert response.json()["event"] == {"kind": "meeting", "start_date": "2026-11-05", "end_date": None}
    post = Post.objects.get()
    assert (post.event_kind, str(post.event_start), post.event_end) == ("meeting", "2026-11-05", None)


def test_post_without_an_event_has_null(api, world):
    response = api(world.users["va_kolakaluru"]).post(POSTS, body(world), format="json")
    assert response.json()["event"] is None and response.json()["is_urgent"] is False


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "camp", "start_date": "2026-11-05", "end_date": "2026-11-04"},   # ends before it starts
        {"kind": "party", "start_date": "2026-11-05"},                            # unknown kind
        {"kind": "camp"},                                                         # no start date
    ],
)
def test_bad_events_are_refused(api, world, event):
    response = api(world.users["va_kolakaluru"]).post(POSTS, body(world, event=event), format="json")
    assert response.status_code == 400 and code(response) == "validation_error"
    assert not Post.objects.exists()


def test_event_can_be_changed_and_cleared_on_a_draft(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    client = api(world.users["pm_kolakaluru"])
    changed = client.patch(
        f"{POSTS}{post.id}/", {"event": {"kind": "camp", "start_date": "2026-11-05", "end_date": "2026-11-07"}}, format="json"
    )
    assert changed.status_code == 200
    assert changed.json()["event"] == {"kind": "camp", "start_date": "2026-11-05", "end_date": "2026-11-07"}
    cleared = client.patch(f"{POSTS}{post.id}/", {"event": None}, format="json")
    assert cleared.json()["event"] is None
    assert Post.objects.get(pk=post.pk).event_start is None
    untouched = client.patch(f"{POSTS}{post.id}/", {"title": "కొత్త శీర్షిక"}, format="json")
    assert untouched.json()["event"] is None


def test_database_refuses_an_end_before_the_start(world, make_post):
    from datetime import date

    post = make_post("va_kolakaluru", "kolakaluru")
    with pytest.raises(IntegrityError), transaction.atomic():
        Post.objects.filter(pk=post.pk).update(event_kind="camp", event_start=date(2026, 11, 5), event_end=date(2026, 11, 1))


# --- Urgent (Q19) -------------------------------------------------------------------------


@pytest.mark.parametrize("author, target", [("va_kolakaluru", "kolakaluru"), ("ma_tenali", "tenali"), ("da_guntur", "guntur")])
def test_admins_may_mark_urgent_when_posting(api, world, author, target):
    response = api(world.users[author]).post(POSTS, body(world, target, is_urgent=True), format="json")
    assert response.status_code == 201
    assert response.json()["is_urgent"] is True
    assert Post.objects.get().is_urgent


def test_panchayat_members_cannot_mark_urgent(api, world, make_post):
    client = api(world.users["pm_kolakaluru"])
    response = client.post(POSTS, body(world, is_urgent=True), format="json")
    assert response.status_code == 403 and code(response) == "permission_denied"
    assert not Post.objects.exists()
    draft = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    refused = client.patch(f"{POSTS}{draft.id}/", {"is_urgent": True}, format="json")
    assert refused.status_code == 403 and code(refused) == "permission_denied"
    assert not Post.objects.get(pk=draft.pk).is_urgent
    assert client.patch(f"{POSTS}{draft.id}/", {"is_urgent": False}, format="json").status_code == 200


def test_admin_can_change_urgent_on_their_own_draft(api, world, make_post):
    draft = make_post("va_kolakaluru", "kolakaluru", submit=False)
    response = api(world.users["va_kolakaluru"]).patch(f"{POSTS}{draft.id}/", {"is_urgent": True}, format="json")
    assert response.status_code == 200 and response.json()["is_urgent"] is True


def mark(api, world, actor, post, value=True):
    return api(world.users[actor]).post(f"{POSTS}{post.id}/urgent/", {"is_urgent": value}, format="json")


def test_reviewer_marks_and_clears_urgent_and_it_is_audited(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    assert post.status == PostStatus.PENDING
    response = mark(api, world, "va_kolakaluru", post)
    assert response.status_code == 200 and response.json()["is_urgent"] is True
    assert mark(api, world, "va_kolakaluru", post).status_code == 200      # same value: no change
    assert AuditLog.objects.filter(action="post.urgent_set").count() == 1
    row = AuditLog.objects.get(action="post.urgent_set")
    assert row.actor_id == world.users["va_kolakaluru"].id and row.actor_role == "VILLAGE_ADMIN"
    assert row.target_id == str(post.id)

    cleared = mark(api, world, "da_guntur", post, False)
    assert cleared.status_code == 200 and cleared.json()["is_urgent"] is False
    assert AuditLog.objects.get(action="post.urgent_cleared").actor_role == "DISTRICT_ADMIN"


def test_urgent_on_a_published_post(api, world, make_post):
    post = make_post("va_kolakaluru", "kolakaluru")
    assert post.status == PostStatus.PUBLISHED
    assert mark(api, world, "ma_tenali", post).json()["is_urgent"] is True


def test_urgent_is_offered_to_reviewers_only(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    reviewer = api(world.users["ma_tenali"]).get(f"{POSTS}{post.id}/").json()
    author = api(world.users["pm_kolakaluru"]).get(f"{POSTS}{post.id}/").json()
    assert "set_urgent" in reviewer["allowed_actions"]
    assert "set_urgent" not in author["allowed_actions"]


def test_panchayat_member_cannot_use_the_urgent_action(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    response = mark(api, world, "pm_kolakaluru", post)
    assert response.status_code == 403 and code(response) == "permission_denied"
    assert not Post.objects.get(pk=post.pk).is_urgent


def test_reviewer_outside_the_area_cannot_mark_urgent(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    for actor in ("va_kaza", "ma_mangalagiri", "da_krishna", "va_nandivelugu"):
        response = mark(api, world, actor, post)
        assert response.status_code in (403, 404), actor
        assert code(response) in ("out_of_area", "not_found"), actor
    assert not Post.objects.get(pk=post.pk).is_urgent


def test_a_draft_cannot_be_marked_urgent_through_review(api, world, make_post):
    draft = make_post("va_kolakaluru", "kolakaluru", submit=False)
    response = mark(api, world, "va_kolakaluru", draft)
    assert response.status_code == 409 and code(response) == "invalid_transition"


# --- Video (Q8) ---------------------------------------------------------------------------


def mp4(name="clip.mp4", size=2048) -> SimpleUploadedFile:
    data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (size - 12)
    return SimpleUploadedFile(name, data, content_type="video/mp4")


def picture(fmt="PNG", size=(1920, 1080)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def ffmpeg(monkeypatch):
    """Pretend ffprobe and ffmpeg are installed: the video is 42 s long."""
    calls = {}

    def probe(path):
        calls["probe"] = path
        return calls.get("length", 42.0)

    def poster(path, at_seconds):
        calls["poster_at"] = at_seconds
        return picture()

    monkeypatch.setattr(media_service, "probe_duration", probe)
    monkeypatch.setattr(media_service, "extract_poster", poster)
    return calls


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(media_service, "probe_duration", lambda path: None)
    monkeypatch.setattr(media_service, "extract_poster", lambda path, at_seconds: None)


def send_video(api, world, post, file=None, actor="pm_kolakaluru", **extra):
    data = {"file": file or mp4(), "kind": "video", **extra}
    return api(world.users[actor]).post(f"{POSTS}{post.id}/media/", data, format="multipart")


def send_photo(api, world, post, actor="pm_kolakaluru"):
    file = SimpleUploadedFile("photo.jpg", picture("JPEG", (800, 600)), content_type="image/jpeg")
    return api(world.users[actor]).post(f"{POSTS}{post.id}/media/", {"file": file}, format="multipart")


def test_video_with_ffmpeg_on_the_server(api, world, make_post, ffmpeg, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = send_video(api, world, post)
    assert response.status_code == 201, response.json()
    data = response.json()
    assert set(data) == {"id", "kind", "url", "poster_url", "thumb_url", "width", "height", "duration_s", "bytes"}
    assert data["kind"] == "video" and data["duration_s"] == 42 and data["bytes"] == 2048
    assert data["url"].endswith(f"{data['id']}.mp4")
    assert data["poster_url"].endswith("-poster-1280.webp") and data["thumb_url"].endswith("-poster-320.webp")
    assert (data["width"], data["height"]) == (1280, 720)
    assert ffmpeg["poster_at"] == 1.0
    media = PostMedia.objects.get()
    assert media.kind == MediaKind.VIDEO and (media_root / media.video.name).exists()
    detail = api(world.users["pm_kolakaluru"]).get(f"{POSTS}{post.id}/").json()
    assert detail["media"][0]["kind"] == "video"


def test_photo_media_says_kind_image(api, world, make_post, ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    data = send_photo(api, world, post).json()
    assert data["kind"] == "image" and data["poster_url"] is None and data["duration_s"] is None


def test_mov_is_accepted(api, world, make_post, ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    data = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 500
    response = send_video(api, world, post, SimpleUploadedFile("clip.MOV", data, content_type="video/quicktime"))
    assert response.status_code == 201 and response.json()["url"].endswith(".mov")


def test_without_ffmpeg_the_phone_sends_length_and_poster(api, world, make_post, no_ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    missing_length = send_video(api, world, post)
    assert missing_length.status_code == 400 and "duration_s" in missing_length.json()["error"]["fields"]
    missing_poster = send_video(api, world, post, duration_s="30.4")
    assert missing_poster.status_code == 400 and "poster" in missing_poster.json()["error"]["fields"]
    poster = SimpleUploadedFile("poster.jpg", picture("JPEG", (720, 1280)), content_type="image/jpeg")
    response = send_video(api, world, post, duration_s="30.4", poster=poster)
    assert response.status_code == 201
    assert response.json()["duration_s"] == 30
    assert (response.json()["width"], response.json()["height"]) == (720, 1280)
    assert PostMedia.objects.count() == 1


def test_measured_length_wins_over_the_phones(api, world, make_post, ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    ffmpeg["length"] = 75.0
    response = send_video(api, world, post, duration_s="20")
    assert response.status_code == 400 and code(response) == "video_too_long"


@pytest.mark.parametrize("length, expected", [(60.4, 201), (61.0, 400)])
def test_at_most_sixty_seconds(api, world, make_post, ffmpeg, length, expected):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    ffmpeg["length"] = length
    response = send_video(api, world, post)
    assert response.status_code == expected
    if expected == 400:
        assert code(response) == "video_too_long"
        assert not PostMedia.objects.exists()


def test_video_too_large(api, world, make_post, ffmpeg, settings):
    settings.MAX_VIDEO_UPLOAD_MB = 1
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = send_video(api, world, post, mp4(size=1024 * 1024 + 1))
    assert response.status_code == 413
    assert "1 MB" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "file",
    [
        SimpleUploadedFile("clip.avi", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100),     # wrong extension
        SimpleUploadedFile("clip.mp4", b"RIFF....AVI LIST" + b"\x00" * 100),             # not an MP4 inside
        SimpleUploadedFile("clip.mp4", b"\x00\x00"),                                     # too short
    ],
)
def test_not_a_video(api, world, make_post, ffmpeg, file):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = send_video(api, world, post, file)
    assert response.status_code == 415


def test_one_video_and_never_with_photos(api, world, make_post, ffmpeg):
    with_video = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    assert send_video(api, world, with_video).status_code == 201
    second = send_video(api, world, with_video)
    assert second.status_code == 409 and code(second) == "media_conflict"
    photo = send_photo(api, world, with_video)
    assert photo.status_code == 409 and code(photo) == "media_conflict"

    with_photo = make_post("pm_kolakaluru", "kolakaluru", submit=False, title="ఫోటో")
    assert send_photo(api, world, with_photo).status_code == 201
    video = send_video(api, world, with_photo)
    assert video.status_code == 409 and code(video) == "media_conflict"


def test_video_posts_can_be_switched_off(api, world, make_post, ffmpeg, settings):
    settings.VIDEO_POSTS_ENABLED = False
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = send_video(api, world, post)
    assert response.status_code == 403 and code(response) == "permission_denied"


def test_only_the_author_adds_a_video(api, world, make_post, ffmpeg):
    post = make_post("pm_kolakaluru", "kolakaluru")
    response = send_video(api, world, post, actor="va_kolakaluru")
    assert response.status_code == 403


def test_removing_the_video_deletes_every_file(api, world, make_post, ffmpeg, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media_id = send_video(api, world, post).json()["id"]
    media = PostMedia.objects.get(pk=media_id)
    files = [media_root / name for name in (media.video.name, media.image.name, media.thumbnail.name)]
    assert all(path.exists() for path in files)
    response = api(world.users["pm_kolakaluru"]).delete(f"{POSTS}{post.id}/media/{media_id}/")
    assert response.status_code == 204
    assert not any(path.exists() for path in files)
    assert send_photo(api, world, post).status_code == 201       # photos are allowed again


# --- "Visible to" (Q5) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "author, target, expected",
    [
        ("da_guntur", "ap", 403), ("da_guntur", "guntur", 201), ("da_guntur", "mangalagiri", 201), ("da_guntur", "kaza", 201),
        ("ma_tenali", "guntur", 403), ("ma_tenali", "tenali", 201), ("ma_tenali", "nandivelugu", 201), ("ma_tenali", "kaza", 403),
        ("va_kolakaluru", "tenali", 403), ("va_kolakaluru", "kolakaluru", 201), ("va_kolakaluru", "nandivelugu", 403),
        ("pm_kolakaluru", "tenali", 403), ("pm_kolakaluru", "kolakaluru", 201), ("pm_kolakaluru", "ap", 403),
    ],
)
def test_visibility_up_to_your_own_level(api, world, author, target, expected):
    response = api(world.users[author]).post(POSTS, body(world, target), format="json")
    assert response.status_code == expected, response.json()


@pytest.mark.parametrize("allowed, expected", [(True, 201), (False, 403)])
def test_only_the_super_admin_posts_to_the_state(api, world, settings, allowed, expected):
    settings.SUPER_ADMIN_CAN_POST = allowed
    assert api(world.users["super"]).post(POSTS, body(world, "ap"), format="json").status_code == expected


def targets(api, world, who):
    response = api(world.users[who]).get(f"{POSTS}targets/")
    assert response.status_code == 200
    return [
        (level["level"], [area["id"] for area in level["areas"]], level.get("pick_via"))
        for level in response.json()["levels"]
    ]


def ids(world, *keys):
    return sorted(world.areas[key].id for key in keys)


def test_targets_for_a_district_admin(api, world):
    assert targets(api, world, "da_guntur") == [
        ("DISTRICT", ids(world, "guntur"), None),
        ("MANDAL", ids(world, "tenali", "mangalagiri"), None),
        ("VILLAGE", [], "geo"),
    ]


def test_targets_for_a_mandal_admin(api, world):
    assert targets(api, world, "ma_tenali") == [
        ("MANDAL", ids(world, "tenali"), None),
        ("VILLAGE", ids(world, "kolakaluru", "nandivelugu"), None),
    ]


@pytest.mark.parametrize("who", ["va_kolakaluru", "pm_kolakaluru"])
def test_targets_for_village_officials(api, world, who):
    assert targets(api, world, who) == [("VILLAGE", ids(world, "kolakaluru"), None)]


def test_targets_for_the_super_admin(api, world, settings):
    settings.SUPER_ADMIN_CAN_POST = False
    assert targets(api, world, "super") == []
    settings.SUPER_ADMIN_CAN_POST = True
    assert targets(api, world, "super") == [
        ("STATE", ids(world, "ap"), None),
        ("DISTRICT", ids(world, "guntur", "krishna"), None),
        ("MANDAL", [], "geo"),
        ("VILLAGE", [], "geo"),
    ]


def test_targets_names_follow_the_language(api, world):
    response = api(world.users["ma_tenali"]).get(f"{POSTS}targets/", HTTP_ACCEPT_LANGUAGE="en")
    assert response.json()["levels"][0]["areas"][0]["name"] == "Tenali"


def test_targets_need_a_login(api, world):
    assert api().get(f"{POSTS}targets/").status_code == 401


def test_every_offered_target_can_be_posted_to(api, world, settings):
    """The picker never offers an area the server would refuse."""
    settings.SUPER_ADMIN_CAN_POST = True
    for who in ("super", "da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"):
        levels = api(world.users[who]).get(f"{POSTS}targets/").json()["levels"]
        for level in levels:
            for area in level["areas"]:
                data = body(world, title="T") | {"target": {"level": area["level"], "id": area["id"]}, "title": "T"}
                assert api(world.users[who]).post(POSTS, data, format="json").status_code == 201, (who, area)

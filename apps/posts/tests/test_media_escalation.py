"""Photos (WebP, 1280 px, 320 px thumbnail, at most 5), escalation, settings, categories."""

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from apps.audit.models import AuditLog
from apps.posts import services, signals
from apps.posts.models import Post, PostStatus, ReviewAction, SettingOverride

pytestmark = pytest.mark.django_db


def jpeg(width=3000, height=2000, exif=True) -> SimpleUploadedFile:
    image = Image.new("RGB", (width, height), (31, 138, 59))
    buffer = io.BytesIO()
    kwargs = {}
    if exif:
        data = Image.Exif()
        data[0x010F] = "PhoneMaker"  # camera make
        data[0x8825] = {2: (16.0, 14.0, 17.0)}  # GPS info
        kwargs["exif"] = data
    image.save(buffer, "JPEG", **kwargs)
    return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")


def upload(api, world, actor, post, file):
    return api(world.users[actor]).post(f"/api/v1/posts/{post.id}/media/", {"file": file}, format="multipart")


def test_photo_is_resized_to_webp_with_a_thumbnail_and_no_exif(api, world, make_post, media_root):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, "pm_kolakaluru", post, jpeg())
    assert response.status_code == 201
    data = response.json()
    assert (data["width"], data["height"]) == (1280, 853)
    assert data["url"].endswith("-1280.webp") and data["thumb_url"].endswith("-320.webp")
    assert data["url"].startswith("http://testserver/media/posts/")

    media = post.media.get()
    with Image.open(media.image.path) as large:
        assert large.format == "WEBP" and max(large.size) == 1280
        assert not large.getexif()
    with Image.open(media.thumbnail.path) as thumb:
        assert thumb.format == "WEBP" and max(thumb.size) == 320

    detail = api(world.users["pm_kolakaluru"]).get(f"/api/v1/posts/{post.id}/").json()
    assert detail["media"][0]["id"] == data["id"]


def test_small_photos_are_not_enlarged(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    data = upload(api, world, "pm_kolakaluru", post, jpeg(800, 600, exif=False)).json()
    assert (data["width"], data["height"]) == (800, 600)


def test_at_most_five_photos(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    for _ in range(5):
        assert upload(api, world, "pm_kolakaluru", post, jpeg(400, 300, exif=False)).status_code == 201
    sixth = upload(api, world, "pm_kolakaluru", post, jpeg(400, 300, exif=False))
    assert sixth.status_code == 400
    assert post.media.count() == 5


def test_not_an_image(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    fake = SimpleUploadedFile("photo.jpg", b"not really a photo", content_type="image/jpeg")
    response = upload(api, world, "pm_kolakaluru", post, fake)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media"


def test_too_large(api, world, make_post, settings):
    settings.MAX_IMAGE_UPLOAD_MB = 0
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    response = upload(api, world, "pm_kolakaluru", post, jpeg(400, 300))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_only_the_author_adds_photos_and_only_while_editable(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")  # PENDING now
    assert upload(api, world, "va_kolakaluru", post, jpeg(400, 300)).status_code == 403
    assert upload(api, world, "pm_kolakaluru", post, jpeg(400, 300)).status_code == 409


def test_remove_photo_deletes_the_files(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    media_id = upload(api, world, "pm_kolakaluru", post, jpeg(400, 300)).json()["id"]
    media = post.media.get()
    path = media.image.path
    response = api(world.users["pm_kolakaluru"]).delete(f"/api/v1/posts/{post.id}/media/{media_id}/")
    assert response.status_code == 204
    assert not post.media.exists()
    import os

    assert not os.path.exists(path)


# --- Escalation ----------------------------------------------------------------------------------


def overdue(post, hours):
    past = timezone.now() - timedelta(hours=hours)
    Post.objects.filter(pk=post.pk).update(submitted_at=past)


def test_escalation_climbs_one_level_per_period(world, make_post, django_capture_on_commit_callbacks):
    post = make_post("pm_kolakaluru", "kolakaluru")
    alerts = []

    def receiver(sender, post, level, **kwargs):
        alerts.append(level)

    signals.post_escalated.connect(receiver)
    try:
        assert services.escalate_overdue() == 0  # not overdue yet
        overdue(post, 25)
        with django_capture_on_commit_callbacks(execute=True):
            assert services.escalate_overdue() == 1
            assert services.escalate_overdue() == 0  # same period: nothing new
        post.refresh_from_db()
        assert post.escalation_level == "MANDAL" and post.escalated_at is not None

        Post.objects.filter(pk=post.pk).update(escalated_at=timezone.now() - timedelta(hours=25))
        with django_capture_on_commit_callbacks(execute=True):
            services.escalate_overdue()
        Post.objects.filter(pk=post.pk).update(escalated_at=timezone.now() - timedelta(hours=25))
        with django_capture_on_commit_callbacks(execute=True):
            services.escalate_overdue()
        Post.objects.filter(pk=post.pk).update(escalated_at=timezone.now() - timedelta(hours=25))
        assert services.escalate_overdue() == 0  # nothing above the state
    finally:
        signals.post_escalated.disconnect(receiver)

    assert alerts == ["MANDAL", "DISTRICT", "STATE"]
    assert ReviewAction.objects.filter(post=post, action="ESCALATE").count() == 3
    assert AuditLog.objects.filter(action="post.escalated", via="system").count() == 3


def test_escalated_post_stays_in_the_lower_queue(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    overdue(post, 25)
    services.escalate_overdue()
    queue = api(world.users["va_kolakaluru"]).get("/api/v1/review/queue/").json()
    assert queue["results"][0]["escalated"] is True
    assert api(world.users["ma_tenali"]).get("/api/v1/review/queue/?escalated=1").json()["count"] == 1


def test_decided_posts_never_escalate(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    overdue(post, 30)
    services.review(world.users["va_kolakaluru"], post, "publish")
    assert services.escalate_overdue() == 0


def test_escalation_hours_come_from_the_runtime_setting(world, make_post):
    SettingOverride.objects.create(key="REVIEW_ESCALATION_HOURS", value=48)
    post = make_post("pm_kolakaluru", "kolakaluru")
    overdue(post, 25)
    assert services.escalate_overdue() == 0
    overdue(post, 49)
    assert services.escalate_overdue() == 1


def test_ensure_schedules_registers_the_job(db):
    from django.core.management import call_command
    from django_q.models import Schedule

    call_command("ensure_schedules", stdout=io.StringIO())
    call_command("ensure_schedules", stdout=io.StringIO())
    schedule = Schedule.objects.get(name="escalate-overdue-posts")
    assert schedule.func == "apps.posts.tasks.run_escalation"
    assert schedule.minutes == 15


# --- Settings and categories ---------------------------------------------------------------------------


def test_settings_for_super_admins_only(api, world):
    assert api(world.users["da_guntur"]).get("/api/v1/admin/settings/").status_code == 403
    client = api(world.users["super"])
    data = client.get("/api/v1/admin/settings/").json()
    assert data["review_escalation_hours"] == 24
    assert data["languages"] == {"te": True, "en": True}

    changed = client.patch("/api/v1/admin/settings/", {"review_escalation_hours": 48, "languages": {"en": False}}, format="json")
    assert changed.status_code == 200
    assert changed.json()["review_escalation_hours"] == 48
    assert changed.json()["languages"] == {"te": True, "en": False}
    assert services.runtime("REVIEW_ESCALATION_HOURS") == 48
    assert AuditLog.objects.filter(action="setting.changed").exists()

    assert client.patch("/api/v1/admin/settings/", {"review_escalation_hours": 5}, format="json").status_code == 400
    assert client.patch("/api/v1/admin/settings/", {"languages": {"te": False}}, format="json").status_code == 400


def test_categories(api, world):
    public = api().get("/api/v1/categories/")
    assert [row["key"] for row in public.json()] == ["announce", "schemes", "news", "updates"]
    assert public.json()[0]["name"] == "గ్రామ చాటింపులు"

    admin = api(world.users["super"])
    rows = admin.get("/api/v1/admin/categories/").json()
    news = next(row for row in rows if row["key"] == "news")
    assert admin.patch(f"/api/v1/admin/categories/{news['id']}/", {"is_active": False}, format="json").status_code == 200
    assert "news" not in [row["key"] for row in api().get("/api/v1/categories/").json()]
    assert api(world.users["da_guntur"]).get("/api/v1/admin/categories/").status_code == 403


def test_disabled_category_cannot_be_used(api, world):
    from apps.posts.models import Category

    Category.objects.filter(key="news").update(is_active=False)
    response = api(world.users["pm_kolakaluru"]).post(
        "/api/v1/posts/",
        {"category": "news", "title": "T", "body": "B", "target": world.area_json("kolakaluru")},
        format="json",
    )
    assert response.status_code == 400
    assert "category" in response.json()["error"]["fields"]
    assert PostStatus.PENDING  # module import sanity

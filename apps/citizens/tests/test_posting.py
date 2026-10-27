"""A citizen phone posts to its village; every post waits for review. Contract section 15."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.citizens.models import CitizenDevice
from apps.posts.models import CITIZEN_ROLE, Post, PostMedia, PostStatus, ReviewAction
from apps.accounts.tokens import issue_tokens

from .conftest import ME, POSTS, photo

pytestmark = pytest.mark.django_db


def code(response):
    return response.json()["error"]["code"]


def test_a_citizen_post_goes_to_review_for_the_phones_village(world, phone, media_root):
    citizen = phone("kolakaluru")
    response = citizen.post(photos=[photo(), photo((800, 600))])
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == "PENDING" and data["category"] == "news" and data["language"] == "te"
    assert data["title"] == "తాగునీటి పైపు పగిలింది"
    assert data["area"] == {"level": "VILLAGE", "id": world.areas["kolakaluru"].id, "name": "కొలకలూరు"}
    assert len(data["photos"]) == 2 and data["photos"][0]["url"].endswith("-1280.webp")
    assert data["photos"][0]["thumb_url"].endswith("-320.webp")
    assert data["reason"] is None and data["decided_at"] is None

    post = Post.objects.get()
    assert post.author is None and str(post.citizen_id) == citizen.device_id
    assert post.posted_as_role == CITIZEN_ROLE and (post.target_level, post.target_area_id) == ("VILLAGE", world.areas["kolakaluru"].id)
    assert post.is_urgent is False and post.event is None
    assert all(media.medium for media in PostMedia.objects.all())  # 640 px for big cards too
    submit = ReviewAction.objects.get(post=post)
    assert (submit.action, submit.actor, submit.actor_role) == ("SUBMIT", None, "CITIZEN")


def test_citizen_posts_are_audited_without_personal_data(world, phone):
    citizen = phone()
    citizen.post()
    created = AuditLog.objects.get(action="post.created")
    submitted = AuditLog.objects.get(action="post.submitted")
    for entry in (created, submitted):
        assert entry.actor is None and entry.via == "citizen"
        assert entry.data["citizen"] == citizen.device_id
    assert citizen.token not in str(AuditLog.objects.values_list("data", flat=True))


@pytest.mark.parametrize("headers", [{}, {"HTTP_X_CITIZEN_TOKEN": "made-up"}, {"HTTP_X_CITIZEN_TOKEN": " "}])
def test_every_citizen_call_needs_a_valid_token(world, headers):
    client = APIClient(**headers)
    for response in (client.get(ME), client.get(POSTS), client.post(POSTS, {"title": "x"}, format="multipart"),
                     client.patch(ME, {"village_id": world.areas["kaza"].id}, format="json")):
        assert response.status_code == 401 and code(response) == "citizen_token_invalid"
    assert not Post.objects.exists()


def test_an_officials_login_is_not_a_citizen_token(world):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(world.users['va_kolakaluru'])['access']}")
    response = client.post(POSTS, {"category": "news", "title": "t", "body": "b"}, format="multipart")
    assert response.status_code == 401 and code(response) == "citizen_token_invalid"


def test_no_daily_limit_by_default(world, phone):
    citizen = phone()
    for index in range(12):
        assert citizen.post(title=f"సమస్య {index}").status_code == 201
    assert citizen.client.get(ME).json()["posts_per_day"] is None


def test_a_daily_limit_can_be_switched_on(world, phone, settings):
    settings.CITIZEN_POSTS_PER_DAY = 3
    citizen = phone()
    for index in range(3):
        assert citizen.post(title=f"సమస్య {index}").status_code == 201
    fourth = citizen.post(title="నాలుగో")
    assert fourth.status_code == 429 and code(fourth) == "throttled"
    assert 86000 < fourth.json()["error"]["retry_after_s"] <= 86401
    assert citizen.client.get(ME).json()["posts_today"] == 3
    assert phone(ip="10.9.9.9").post().status_code == 201  # another phone is not affected
    Post.objects.filter(citizen_id=citizen.device_id).update(created_at=Post.objects.first().created_at - timedelta(hours=25))
    assert citizen.post(title="మరుసటి రోజు").status_code == 201


@pytest.mark.parametrize(
    "fields, bad",
    [
        ({"title": "   "}, "title"),
        ({"body": ""}, "body"),
        ({"title": "అ" * 151}, "title"),
        ({"category": "nope"}, "category"),
        ({"language": "hi"}, "language"),
    ],
)
def test_fields_are_checked(world, phone, fields, bad):
    response = phone().post(**fields)
    assert response.status_code == 400 and bad in response.json()["error"]["fields"]
    assert not Post.objects.exists()


def test_no_photo_limit_by_default(world, phone, media_root):
    response = phone().post(photos=[photo((400, 300)) for _ in range(8)])
    assert response.status_code == 201 and len(response.json()["photos"]) == 8
    assert [m.order for m in PostMedia.objects.order_by("order")] == list(range(8))


def test_a_photo_limit_can_be_switched_on(world, phone, media_root, settings):
    settings.CITIZEN_MAX_PHOTOS = 3
    response = phone().post(photos=[photo((400, 300)) for _ in range(4)])
    assert response.status_code == 400 and "photos" in response.json()["error"]["fields"]
    assert not Post.objects.exists() and not PostMedia.objects.exists()


def test_a_bad_photo_leaves_no_post_behind(world, phone, media_root):
    fake = SimpleUploadedFile("photo.jpg", b"not a photo", content_type="image/jpeg")
    response = phone().post(photos=[photo(), fake])
    assert response.status_code == 415 and code(response) == "unsupported_media"
    assert not Post.objects.exists() and not PostMedia.objects.exists()


def test_officials_fields_are_ignored(world, phone):
    """A phone cannot pick the target, mark urgent or add an event: only its village, plain post."""
    citizen = phone("kolakaluru")
    citizen.post(is_urgent="true", target_level="STATE", target_area_id=world.areas["ap"].id, status="PUBLISHED")
    post = Post.objects.get()
    assert (post.target_level, post.status, post.is_urgent) == ("VILLAGE", PostStatus.PENDING, False)


def test_moving_village_moves_where_posts_go(world, phone):
    citizen = phone("kolakaluru")
    response = citizen.client.patch(ME, {"village_id": world.areas["kaza"].id}, format="json")
    assert response.status_code == 200 and response.json()["village"]["id"] == world.areas["kaza"].id
    citizen.post()
    assert Post.objects.get().target_area_id == world.areas["kaza"].id
    bad = citizen.client.patch(ME, {"village_id": 999999}, format="json")
    assert bad.status_code == 400 and "village_id" in bad.json()["error"]["fields"]


def test_my_posts_are_mine_only_newest_first(world, phone):
    mine, other = phone(), phone(ip="10.1.1.1")
    mine.post(title="మొదటి")
    mine.post(title="రెండవ")
    other.post(title="వేరే")
    titles = [row["title"] for row in mine.client.get(POSTS).json()]
    assert titles == ["రెండవ", "మొదటి"]


def test_a_blocked_phone_cannot_post_but_can_read(world, phone):
    citizen = phone()
    citizen.post()
    CitizenDevice.objects.update(blocked_at=Post.objects.get().created_at)
    response = citizen.post(title="మళ్ళీ")
    assert response.status_code == 403 and code(response) == "citizen_blocked"
    assert citizen.client.get(ME).json()["blocked"] is True
    assert len(citizen.client.get(POSTS).json()) == 1


def test_citizen_posts_can_be_switched_off(world, phone, settings):
    settings.CITIZEN_POSTS_ENABLED = False
    response = phone().post()
    assert response.status_code == 403 and code(response) == "permission_denied"

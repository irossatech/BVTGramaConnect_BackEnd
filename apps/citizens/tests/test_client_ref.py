"""A citizen post sent again after a lost answer (client_ref, MOB-ERR-004): one post, not two."""

import uuid

import pytest

from apps.audit.models import AuditLog
from apps.posts import services as post_services
from apps.posts.models import Post, PostMedia

from .conftest import ME, photo

pytestmark = pytest.mark.django_db


def test_the_same_client_ref_twice_is_one_post(world, phone):
    citizen = phone()
    ref = str(uuid.uuid4())
    first = citizen.post(client_ref=ref)
    assert first.status_code == 201
    again = citizen.post(client_ref=ref)
    assert again.status_code == 200
    assert again.json() == first.json()  # the same body the first send would have had
    assert Post.objects.filter(citizen_id=citizen.device_id).count() == 1
    assert AuditLog.objects.filter(action="post.created").count() == 1


def test_a_repeat_does_not_use_a_daily_post(world, phone, settings):
    settings.CITIZEN_POSTS_PER_DAY = 1
    citizen = phone()
    ref = str(uuid.uuid4())
    assert citizen.post(client_ref=ref).status_code == 201
    # At the limit, the repeat of the last allowed post still answers: it is the same post.
    assert citizen.post(client_ref=ref).status_code == 200
    assert citizen.client.get(ME).json()["posts_today"] == 1
    # A new post is still refused.
    assert citizen.post(title="కొత్తది", client_ref=str(uuid.uuid4())).status_code == 429


def test_the_repeats_photos_are_not_stored_again(world, phone, media_root):
    citizen = phone()
    ref = str(uuid.uuid4())
    first = citizen.post(client_ref=ref, photos=[photo()])
    assert first.status_code == 201
    again = citizen.post(client_ref=ref, photos=[photo()])
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert PostMedia.objects.filter(post_id=first.json()["id"]).count() == 1
    assert len(again.json()["photos"]) == 1


def test_a_repeat_answers_with_the_post_as_it_is_now(api, world, phone):
    citizen = phone()
    ref = str(uuid.uuid4())
    post_id = citizen.post(client_ref=ref).json()["id"]
    published = api(world.users["va_kolakaluru"]).post(f"/api/v1/posts/{post_id}/review/", {"action": "publish"}, format="json")
    assert published.status_code == 200
    again = citizen.post(client_ref=ref)
    assert again.status_code == 200 and again.json()["status"] == "PUBLISHED"


def test_another_phone_may_use_the_same_client_ref(world, phone):
    ref = str(uuid.uuid4())
    one, two = phone(), phone("nandivelugu", ip="10.9.9.9")
    assert one.post(client_ref=ref).status_code == 201
    assert two.post(client_ref=ref).status_code == 201
    assert Post.objects.filter(client_ref=ref).count() == 2


def test_without_client_ref_every_send_is_a_new_post(world, phone):
    citizen = phone()
    assert citizen.post().status_code == 201
    assert citizen.post().status_code == 201  # older apps: as before
    assert Post.objects.filter(citizen_id=citizen.device_id).count() == 2


def test_a_client_ref_that_is_not_a_uuid_is_refused(world, phone):
    response = phone().post(client_ref="not-a-uuid")
    assert response.status_code == 400
    assert "client_ref" in response.json()["error"]["fields"]


def test_two_repeats_at_the_same_moment_make_one_post(world, phone, monkeypatch):
    citizen = phone()
    ref = str(uuid.uuid4())
    first = citizen.post(client_ref=ref).json()
    # The second request looked before the first was saved, so its first look finds nothing;
    # the unique constraint then stops its insert, and it answers with the first post.
    real = post_services.earlier_post
    looks = []

    def first_look_misses(client_ref, **owner):
        looks.append(client_ref)
        return None if len(looks) == 1 else real(client_ref, **owner)

    monkeypatch.setattr(post_services, "earlier_post", first_look_misses)
    again = citizen.post(client_ref=ref)
    assert len(looks) == 2
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert Post.objects.filter(citizen_id=citizen.device_id).count() == 1

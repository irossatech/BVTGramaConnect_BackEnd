"""An official's post sent again after a lost answer (client_ref, MOB-ERR-004): one post, not two."""

import uuid

import pytest

from apps.audit.models import AuditLog
from apps.posts import services
from apps.posts.models import Post

pytestmark = pytest.mark.django_db

POSTS = "/api/v1/posts/"


def body(world, **extra):
    return {"category": "announce", "language": "te", "title": "గ్రామ సభ", "body": "అక్టోబర్ 20న పంచాయతీ కార్యాలయంలో.",
            "targets": [world.area_json("kolakaluru")], **extra}


def test_the_same_client_ref_twice_is_one_post(api, world):
    client = api(world.users["va_kolakaluru"])
    ref = str(uuid.uuid4())
    first = client.post(POSTS, body(world, client_ref=ref), format="json")
    assert first.status_code == 201 and first.json()["status"] == "PUBLISHED"  # admins publish at once
    again = client.post(POSTS, body(world, client_ref=ref), format="json")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert Post.objects.filter(author=world.users["va_kolakaluru"]).count() == 1
    assert AuditLog.objects.filter(action="post.created").count() == 1
    assert AuditLog.objects.filter(action="post.published").count() <= 1


def test_a_panchayat_members_repeat_is_not_sent_for_review_twice(api, world):
    client = api(world.users["pm_kolakaluru"])
    ref = str(uuid.uuid4())
    first = client.post(POSTS, body(world, client_ref=ref), format="json")
    assert first.status_code == 201 and first.json()["status"] == "PENDING"
    assert client.post(POSTS, body(world, client_ref=ref), format="json").status_code == 200
    assert Post.objects.filter(status="PENDING").count() == 1


def test_two_officials_may_use_the_same_client_ref(api, world):
    ref = str(uuid.uuid4())
    assert api(world.users["va_kolakaluru"]).post(POSTS, body(world, client_ref=ref), format="json").status_code == 201
    assert api(world.users["pm_kolakaluru"]).post(POSTS, body(world, client_ref=ref), format="json").status_code == 201
    assert Post.objects.filter(client_ref=ref).count() == 2


def test_without_client_ref_every_create_is_a_new_post(api, world):
    client = api(world.users["va_kolakaluru"])  # the console sends no client_ref
    assert client.post(POSTS, body(world), format="json").status_code == 201
    assert client.post(POSTS, body(world), format="json").status_code == 201
    assert Post.objects.filter(author=world.users["va_kolakaluru"]).count() == 2


def test_a_client_ref_that_is_not_a_uuid_is_refused(api, world):
    response = api(world.users["va_kolakaluru"]).post(POSTS, body(world, client_ref="abc"), format="json")
    assert response.status_code == 400
    assert "client_ref" in response.json()["error"]["fields"]


def test_an_edit_cannot_change_the_client_ref(api, world):
    client = api(world.users["pm_kolakaluru"])
    ref = str(uuid.uuid4())
    draft = client.post(POSTS, body(world, client_ref=ref, submit=False), format="json").json()
    edited = client.patch(f"{POSTS}{draft['id']}/", {"title": "మార్చిన శీర్షిక", "client_ref": str(uuid.uuid4())}, format="json")
    assert edited.status_code == 200
    assert str(Post.objects.get(pk=draft["id"]).client_ref) == ref


def test_two_repeats_at_the_same_moment_make_one_post(api, world, monkeypatch):
    client = api(world.users["va_kolakaluru"])
    ref = str(uuid.uuid4())
    first = client.post(POSTS, body(world, client_ref=ref), format="json").json()
    real = services.earlier_post
    looks = []

    def first_look_misses(client_ref, **owner):
        looks.append(client_ref)
        return None if len(looks) == 1 else real(client_ref, **owner)

    monkeypatch.setattr(services, "earlier_post", first_look_misses)
    again = client.post(POSTS, body(world, client_ref=ref), format="json")
    assert len(looks) == 2
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert Post.objects.filter(author=world.users["va_kolakaluru"]).count() == 1

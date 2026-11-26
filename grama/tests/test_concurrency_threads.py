"""Requests that really arrive together, in threads, on a real server database (audit stage S6, BVTGC-17).

The other race tests (test_limits_under_concurrency.py, test_small_races.py, ...) replay a race step by
step, which is exact but cannot show what row locks and transaction isolation do. These start several
requests at the same instant from threads, each with its own database connection, and check that the
first decision wins, limits hold and nothing is stored twice.

They need MySQL, MariaDB or PostgreSQL: SQLite lets one writer in at a time and so hides locking (and
its in-memory test database is not shared between threads). CI runs them on MariaDB 11.4 and
PostgreSQL 17 (.github/workflows/tests.yml, TEST_DATABASE_URL); on SQLite they are skipped with that
reason. Each test commits for real (transaction=True) and puts the seed data back afterwards
(serialized_rollback=True).
"""

from __future__ import annotations

import io
import threading

import pytest
from django.db import connection, connections
from rest_framework.test import APIClient

from apps.citizens.tests.conftest import register
from apps.notifications.models import Device
from apps.posts.models import Post, PostMedia, PostStatus, ReviewAction
from conftest import client_for

pytestmark = [
    pytest.mark.skipif(connection.vendor == "sqlite",
                       reason="needs MySQL, MariaDB or PostgreSQL (TEST_DATABASE_URL): SQLite hides locking"),
    pytest.mark.django_db(transaction=True, serialized_rollback=True),
]


def together(*calls):
    """Run every call at the same instant, one thread each; return their results in order."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(index, call):
        try:
            barrier.wait(timeout=30)
            results[index] = call()
        except Exception as error:  # the test reads it from the results
            results[index] = error
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(index, call)) for index, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return results


def statuses(results) -> list:
    return sorted(getattr(result, "status_code", repr(result)) for result in results)


def test_two_reviewers_publishing_the_same_post_at_once_one_wins(world, make_post_pending):
    post = make_post_pending()
    reviewers = [client_for(world.users[name]) for name in ("va_kolakaluru", "ma_tenali", "da_guntur", "super")]
    results = together(*[
        (lambda client=client: client.post(f"/api/v1/posts/{post.id}/review/", {"action": "publish"}, format="json"))
        for client in reviewers
    ])
    assert statuses(results) == [200, 409, 409, 409], [getattr(r, "content", r) for r in results]
    assert all(r.json()["error"]["code"] == "already_decided" for r in results if r.status_code == 409)
    post.refresh_from_db()
    assert post.status == PostStatus.PUBLISHED
    assert ReviewAction.objects.filter(post=post, action=ReviewAction.Action.PUBLISH).count() == 1


def test_a_phone_cannot_pass_its_daily_limit_by_sending_at_once(world, settings):
    from apps.posts.models import SettingOverride

    SettingOverride.objects.update_or_create(key="CITIZEN_POSTS_PER_DAY", defaults={"value": 3})
    token = register(world).json()["token"]
    phone = [APIClient(HTTP_X_CITIZEN_TOKEN=token) for _ in range(8)]
    body = {"category": "news", "language": "te", "title": "తాగునీరు", "body": "పైపు పగిలింది."}
    results = together(*[(lambda client=client: client.post("/api/v1/citizen/posts/", body, format="json")) for client in phone])
    created = [r for r in results if getattr(r, "status_code", None) == 201]
    refused = [r for r in results if getattr(r, "status_code", None) == 429]
    assert len(created) == 3 and len(refused) == 5, statuses(results)
    assert Post.objects.filter(citizen__isnull=False).count() == 3


def test_the_same_push_token_registered_at_once_is_stored_once(world):
    body = {"fcm_token": "same-token-from-a-double-tap", "platform": "android", "language": "te",
            "village_id": world.ref("kolakaluru").id}
    results = together(*[(lambda: APIClient().post("/api/v1/devices/", body, format="json")) for _ in range(6)])
    assert all(getattr(r, "status_code", None) in (200, 201, 409) for r in results), statuses(results)
    assert Device.objects.filter(fcm_token=body["fcm_token"]).count() == 1


def test_photos_uploaded_at_once_never_pass_the_limit(world, settings, tmp_path, make_post_pending):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path / "media"
    post = make_post_pending(submit=False)

    def photo():
        buffer = io.BytesIO()
        Image.new("RGB", (400, 300), (31, 138, 59)).save(buffer, "JPEG")
        return SimpleUploadedFile("photo.jpg", buffer.getvalue(), content_type="image/jpeg")

    author = world.users["pm_kolakaluru"]
    results = together(*[
        (lambda: client_for(author).post(f"/api/v1/posts/{post.id}/media/", {"file": photo()}, format="multipart"))
        for _ in range(6)
    ])
    created = [r for r in results if getattr(r, "status_code", None) == 201]
    assert len(created) == settings.MAX_PHOTOS_PER_POST, statuses(results)
    assert PostMedia.objects.filter(post=post).count() == settings.MAX_PHOTOS_PER_POST


@pytest.fixture
def make_post_pending(world):
    """A Panchayat member's post for Kolakaluru, waiting for review (or a draft with submit=False)."""
    from apps.posts.models import Category
    from apps.posts.services import PostInput, create_post

    def make(submit: bool = True):
        news = Category.objects.get(key="news")
        return create_post(
            world.users["pm_kolakaluru"],
            PostInput(category=news, language="te", title="గ్రామ సభ", body="వివరాలు", target=world.ref("kolakaluru")),
            submit=submit,
        )

    return make

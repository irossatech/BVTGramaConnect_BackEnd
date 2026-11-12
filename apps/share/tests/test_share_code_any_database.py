"""Share codes behave the same on every database (audit finding BE-DB-020).

New codes use small letters and digits only, so a database that ignores case (MySQL's usual
collation) and one that does not (SQLite, PostgreSQL) agree on which codes are equal. A new code
that is already taken is replaced, not turned into a failed publish. Older mixed-case codes keep working.
"""

import re

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.posts import sharing
from apps.posts.models import Category, Post, PostStatus
from apps.posts.services import PostInput, create_post, review

from .test_share import page, publish

pytestmark = pytest.mark.django_db


def codes(monkeypatch, *values):
    """new_share_code() hands out these codes in turn."""
    queue = list(values)
    monkeypatch.setattr(sharing, "new_share_code", lambda: queue.pop(0))


def taken(world, code):
    """A published post already holding `code`."""
    post = publish(world, title="ముందటి వార్త")
    Post.objects.filter(pk=post.pk).update(share_code=code)
    return post


def pending(world):
    return create_post(world.users["pm_kolakaluru"], PostInput(
        category=Category.objects.get(key="announce"), language="te", title="రోడ్డు మరమ్మతు", body="వివరాలు.",
        target=world.ref("kolakaluru"),
    ))


def test_new_codes_are_small_letters_and_digits():
    for _ in range(2000):
        assert re.fullmatch(r"[a-z0-9]{8}", sharing.new_share_code())


def test_an_admin_post_gets_a_fresh_code_when_the_new_one_is_taken(world, monkeypatch):
    taken(world, "abcd1234")
    codes(monkeypatch, "abcd1234", "efgh5678")
    post = publish(world)
    assert post.status == PostStatus.PUBLISHED and post.share_code == "efgh5678"


def test_a_reviewed_post_gets_a_fresh_code_when_the_new_one_is_taken(world, monkeypatch):
    taken(world, "abcd1234")
    post = pending(world)
    codes(monkeypatch, "abcd1234", "abcd1234", "zz99zz99")
    published = review(world.users["va_kolakaluru"], post, "publish")
    assert published.status == PostStatus.PUBLISHED and published.share_code == "zz99zz99"


def test_a_reversed_post_gets_a_fresh_code_when_the_new_one_is_taken(world, monkeypatch):
    taken(world, "abcd1234")
    post = pending(world)
    discarded = review(world.users["va_kolakaluru"], post, "discard", "నకిలీ")
    codes(monkeypatch, "abcd1234", "qwer7890")
    again = review(world.users["super"], discarded, "reverse", "సరైనదే", to_status=PostStatus.PUBLISHED)
    assert again.status == PostStatus.PUBLISHED and again.share_code == "qwer7890"


def test_after_three_clashes_nothing_is_saved(world, monkeypatch):
    taken(world, "abcd1234")
    post = pending(world)
    codes(monkeypatch, *["abcd1234"] * sharing.CODE_TRIES)
    with pytest.raises(IntegrityError):
        review(world.users["va_kolakaluru"], post, "publish")
    post.refresh_from_db()
    assert post.status == PostStatus.PENDING and post.share_code is None


def test_an_older_mixed_case_code_still_opens(world):
    post = taken(world, "AbCd1234")
    assert page("AbCd1234").status_code == 200
    assert APIClient().get("/api/v1/feed/share/AbCd1234/").json()["id"] == str(post.id)

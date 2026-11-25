"""Citizens cannot post in an officials-only category such as Announcements (BVTGC-101).

The app hides it; the server refuses it too, so a post sent straight to the API cannot come out labelled
as an official announcement. Officials still post announcements as before.
"""

import pytest

from apps.posts.models import Post

pytestmark = pytest.mark.django_db


def test_a_citizen_announcement_is_refused(world, phone):
    response = phone("kolakaluru").post(category="announce")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error" and "category" in error["fields"]
    assert not Post.objects.exists()


def test_the_refusal_is_in_telugu_by_default_and_english_on_request(world, phone, english):
    response = phone("kolakaluru").post(category="announce")
    assert response.json()["error"]["fields"]["category"] == ["Citizens cannot post in this category."]


def test_a_refused_post_does_not_count_towards_the_daily_limit(world, phone, settings):
    settings.CITIZEN_POSTS_PER_DAY = 1
    citizen = phone("kolakaluru")
    assert citizen.post(category="announce").status_code == 400
    assert citizen.post().status_code == 201


def test_every_other_category_still_works_for_citizens(world, phone, settings):
    from apps.posts.models import Category

    settings.CITIZEN_POSTS_PER_DAY = 100
    keys = Category.objects.filter(is_active=True).exclude(key__in=["announce"]).values_list("key", flat=True)
    assert keys
    citizen = phone("kolakaluru")
    for key in keys:
        assert citizen.post(category=key).status_code == 201, key


def test_the_list_comes_from_the_setting(world, phone, settings):
    settings.CITIZEN_EXCLUDED_CATEGORIES = ["news"]
    citizen = phone("kolakaluru")
    assert citizen.post(category="news").status_code == 400
    assert citizen.post(category="announce").status_code == 201


def test_officials_still_post_announcements(api, world):
    body = {"category": "announce", "title": "గ్రామ సభ", "body": "రేపు", "targets": [world.area_json("kolakaluru")]}
    response = api(world.users["pm_kolakaluru"]).post("/api/v1/posts/", body, format="json")
    assert response.status_code == 201, response.json()

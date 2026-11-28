"""Fixtures for posts tests. The shared map and officials come from the root conftest.py."""

import pytest

from apps.posts.models import Category
from apps.posts.services import PostInput, create_post


@pytest.fixture
def announce(db):
    return Category.objects.get(key="announce")


@pytest.fixture
def make_post(world, announce):
    """make_post("pm_kolakaluru", "kolakaluru") writes and submits a post as that official."""

    def make(author: str, target: str, *, submit: bool = True, title: str = "గ్రామ సభ సమావేశం"):
        return create_post(
            world.users[author],
            PostInput(category=announce, language="te", title=title, body="అక్టోబర్ 20న గ్రామ సభ.", target=world.ref(target)),
            submit=submit,
        )

    return make


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT

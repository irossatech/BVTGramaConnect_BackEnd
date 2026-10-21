from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import OFFICIALS
from apps.posts.models import Category, Post, PostMedia, PostStatus


@pytest.fixture
def make_post(world):
    """make_post("tenali", status=..., minutes_ago=..., media=2) -> Post aimed at that area."""
    counter = {"n": 0}

    def make(area_key, *, status=PostStatus.PUBLISHED, minutes_ago=0, author="va_kolakaluru",
             category="announce", media=0, title=None, language="te"):
        counter["n"] += 1
        role, author_area = OFFICIALS[author]
        target = world.ref(area_key)
        when = timezone.now() - timedelta(minutes=minutes_ago)
        post = Post.objects.create(
            author=world.users[author],
            posted_as_role=role,
            posted_as_level=world.ref(author_area).level,
            posted_as_area_id=world.ref(author_area).id,
            category=Category.objects.get(key=category),
            language=language,
            title=title or f"పోస్ట్ {counter['n']} ({area_key})",
            body="గ్రామ సభ రేపు ఉదయం 10 గంటలకు.",
            target_level=target.level,
            target_area_id=target.id,
            status=status,
            published_at=when if status in (PostStatus.PUBLISHED, PostStatus.UNPUBLISHED) else None,
        )
        for index in range(media):
            PostMedia.objects.create(
                post=post,
                image=f"posts/2026/10/{post.pk}-{index}.webp",
                thumbnail=f"posts/2026/10/{post.pk}-{index}-320.webp",
                width=1280, height=720, bytes=50000, order=index,
            )
        return post

    return make

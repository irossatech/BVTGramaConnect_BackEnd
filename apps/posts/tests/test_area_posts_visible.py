"""Officials see published posts that reach their area (backend plan of 2026-11-20, test item 1).

A District Admin's post to the district, or a post to Everyone, is published at once and citizens of
every village below see it. Officials of those villages and mandals now see it too in `GET /posts/`,
read only: no new action. Only PUBLISHED is added; a pending or discarded post above their area stays
hidden from them as before.

Expected answers come from the rules and the map's parent links in conftest.py, not from posts.services.
"""

import pytest

from apps.accounts.roles import Role
from apps.posts.models import PostStatus
from conftest import OFFICIALS, inside

pytestmark = pytest.mark.django_db

REVIEWERS = {Role.SUPER_ADMIN, Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN}
ACTORS = list(OFFICIALS)

#: (author, target): a District Admin's post to their district, a Super Admin's post to Everyone.
CASES = [("da_guntur", "guntur"), ("super", "ap")]


def reviews(actor: str, target: str) -> bool:
    """The post's area lies inside the actor's area, and the actor's role reviews (the old rule)."""
    role, area = OFFICIALS[actor]
    return role in REVIEWERS and (role == Role.SUPER_ADMIN or inside(target, area))


def covered(actor: str, target: str) -> bool:
    """The actor's area is the post's area or lies inside it (the new rule)."""
    return inside(OFFICIALS[actor][1], target)


@pytest.fixture
def published(make_post):
    posts = {case: make_post(*case) for case in CASES}
    assert all(post.status == PostStatus.PUBLISHED for post in posts.values())
    return posts


def listed(client, post) -> bool:
    ids = {row["id"] for row in client.get("/api/v1/posts/?page_size=100").json()["results"]}
    return str(post.id) in ids


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("actor", ACTORS)
def test_a_published_post_is_seen_by_every_official_below_it(api, world, published, case, actor):
    author, target = case
    post = published[case]
    client = api(world.users[actor])
    sees = actor == author or reviews(actor, target) or covered(actor, target)

    assert listed(client, post) is sees
    read = client.get(f"/api/v1/posts/{post.id}/")
    assert read.status_code == (200 if sees else 404)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("actor", ACTORS)
def test_seeing_it_gives_no_new_action(api, world, published, case, actor):
    author, target = case
    post = published[case]
    if actor == author or reviews(actor, target) or not covered(actor, target):
        pytest.skip("only officials who see the post through the new rule")
    client = api(world.users[actor])
    assert client.get(f"/api/v1/posts/{post.id}/").json()["allowed_actions"] == []
    for action in ("unpublish", "recheck", "discard"):
        response = client.post(f"/api/v1/posts/{post.id}/review/", {"action": action, "comment": "తప్పు"}, format="json")
        assert response.status_code == 403, (action, response.json())
    assert client.post(f"/api/v1/posts/{post.id}/urgent/", {"is_urgent": True}, format="json").status_code == 403
    assert client.patch(f"/api/v1/posts/{post.id}/", {"title": "మార్చారు"}, format="json").status_code == 403
    post.refresh_from_db()
    assert post.status == PostStatus.PUBLISHED and not post.is_urgent


@pytest.mark.parametrize("actor", ["va_kolakaluru", "ma_tenali", "pm_kolakaluru", "da_guntur", "va_kaza"])
def test_mine_stays_the_users_own_posts(api, world, published, actor):
    client = api(world.users[actor])
    ids = {row["id"] for row in client.get("/api/v1/posts/?mine=1&page_size=100").json()["results"]}
    own = {str(post.id) for (author, _), post in published.items() if author == actor}
    assert ids == own


@pytest.mark.parametrize("final", [PostStatus.PENDING, PostStatus.DISCARDED])
def test_only_published_posts_are_added(api, world, settings, make_post, final):
    """A Panchayat member's post to the whole district, pending or discarded: officials below the district
    who do not review it (another village's admin and member) still do not see it."""
    post = make_post("pm_kolakaluru", "guntur")
    assert post.status == PostStatus.PENDING
    if final == PostStatus.DISCARDED:
        response = api(world.users["super"]).post(
            f"/api/v1/posts/{post.id}/review/", {"action": "discard", "comment": "నకిలీ"}, format="json"
        )
        assert response.status_code == 200
    for actor in ("va_kaza", "pm_kaza", "ma_mangalagiri", "pm_nandivelugu"):
        client = api(world.users[actor])
        assert not listed(client, post), actor
        assert client.get(f"/api/v1/posts/{post.id}/").status_code == 404, actor
    # Its reviewers still see it as before.
    assert listed(api(world.users["da_guntur"]), post)


def test_a_published_village_post_is_not_seen_from_the_mandal_level_down_elsewhere(api, world, make_post):
    """A Village Admin's post to their own village reaches only that village: officials of a sibling
    village do not get it; the mandal and district above review it, as before."""
    post = make_post("va_kolakaluru", "kolakaluru")
    assert post.status == PostStatus.PUBLISHED
    for actor, sees in (("pm_kolakaluru", True), ("va_nandivelugu", False), ("pm_nandivelugu", False),
                        ("ma_tenali", True), ("da_guntur", True), ("ma_mangalagiri", False), ("da_krishna", False)):
        assert listed(api(world.users[actor]), post) is sees, actor

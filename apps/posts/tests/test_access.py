"""Cross-area access: every official against a village post and a mandal post.

Expected answers come from CLAUDE.md's rules and the map's parent links in
conftest.py, not from posts.services.
"""

import pytest

from apps.accounts.roles import Role
from conftest import OFFICIALS, inside

pytestmark = pytest.mark.django_db

REVIEWERS = {Role.SUPER_ADMIN, Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN}
ACTORS = list(OFFICIALS)


def reviews(actor: str, target: str) -> bool:
    role, area = OFFICIALS[actor]
    return role in REVIEWERS and (role == Role.SUPER_ADMIN or inside(target, area))


CASES = [
    # (author, target): a village post by a member, a mandal post by a mandal admin
    ("pm_kolakaluru", "kolakaluru"),
    ("ma_tenali", "tenali"),
]


@pytest.fixture
def pending(settings, make_post):
    settings.ADMIN_POSTS_AUTO_PUBLISH = False
    return {case: make_post(*case) for case in CASES}


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("actor", ACTORS)
def test_read_queue_and_publish(api, world, pending, case, actor):
    author, target = case
    post = pending[case]
    client = api(world.users[actor])
    is_author = actor == author
    can_review = reviews(actor, target)

    read = client.get(f"/api/v1/posts/{post.id}/")
    assert read.status_code == (200 if (is_author or can_review) else 404)

    queue = client.get("/api/v1/review/queue/?page_size=100")
    if OFFICIALS[actor][0] not in REVIEWERS:
        assert queue.status_code == 403
    else:
        ids = {row["id"] for row in queue.json()["results"]}
        assert (str(post.id) in ids) == (can_review and not is_author)

    publish = client.post(f"/api/v1/posts/{post.id}/review/", {"action": "publish"}, format="json")
    if can_review and not is_author:
        assert publish.status_code == 200
    elif is_author:
        assert publish.status_code == 403
    else:
        assert publish.status_code == 404


@pytest.mark.parametrize("actor", ACTORS)
def test_take_down_matrix(api, world, make_post, actor):
    post = make_post("va_kolakaluru", "kolakaluru")  # auto-published
    response = api(world.users[actor]).post(
        f"/api/v1/posts/{post.id}/review/", {"action": "unpublish", "comment": "తప్పు"}, format="json"
    )
    if reviews(actor, "kolakaluru"):
        assert response.status_code == 200
    elif actor == "va_kolakaluru":
        assert response.status_code == 200
    elif OFFICIALS[actor][0] in REVIEWERS:
        assert response.status_code == 404  # outside the area: not even visible
    else:
        assert response.status_code == 404


@pytest.mark.parametrize("actor, expected", [("va_kolakaluru", 200), ("ma_tenali", 200), ("va_kaza", 403), ("pm_kolakaluru", 200)])
def test_area_stats(api, world, make_post, actor, expected):
    make_post("pm_kolakaluru", "kolakaluru")
    tenali = world.areas["tenali"]
    query = "" if actor == "pm_kolakaluru" else f"?level=MANDAL&area_id={tenali.id}"
    if actor == "va_kolakaluru":
        query = ""
    response = api(world.users[actor]).get(f"/api/v1/stats/{query}")
    assert response.status_code == expected
    if expected == 200 and actor != "pm_kolakaluru":
        assert response.json()["pending_review"] == 1

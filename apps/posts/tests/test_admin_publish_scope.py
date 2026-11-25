"""Where an admin's post goes live at once, and where it is reviewed (BVTGC-26, Admin's decision of 2026-11-25).

The rule, worked out here from the map and the roles, not by calling the code under test:
- the Super Admin's post goes live at once anywhere (their area is the whole state);
- a District, Mandal or Village Admin's post goes live at once when every area it is for lies inside
  their own area; one area outside it, or Everyone (the state), and the whole post goes to review;
- a Panchayat member's post is always reviewed.
POST /posts/targets/check/ answers the same before the post is sent (for the app's note, BVTGC-104).
"""

import pytest

from apps.accounts.roles import Role
from apps.posts.models import PostStatus
from conftest import OFFICIALS, PARENT, inside

pytestmark = pytest.mark.django_db
POSTS = "/api/v1/posts/"
CHECK = "/api/v1/posts/targets/check/"
AREAS = ["ap", *PARENT]  # the state, both districts, every mandal and village
ADMINS = (Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN)


def expected_live(author: str, targets: list[str]) -> bool:
    role, own = OFFICIALS[author]
    if role == Role.SUPER_ADMIN:
        return True
    if role in ADMINS:
        return all(inside(target, own) for target in targets)
    return False  # Panchayat member


def send(api, world, author, targets):
    body = {"targets": [world.area_json(key) for key in targets]}
    check = api(world.users[author]).post(CHECK, body, format="json")
    created = api(world.users[author]).post(POSTS, {"category": "news", "title": "T", "body": "B", **body}, format="json")
    assert check.status_code == 200, check.json()
    assert created.status_code == 201, created.json()
    return check.json()["publishes_at_once"], created.json()["status"]


@pytest.mark.parametrize("author", list(OFFICIALS))
def test_every_official_for_every_single_area(api, world, author):
    for target in AREAS:
        live = expected_live(author, [target])
        said, status = send(api, world, author, [target])
        assert said is live, (author, target)
        assert status == (PostStatus.PUBLISHED if live else PostStatus.PENDING), (author, target)


@pytest.mark.parametrize(
    ("author", "targets"),
    [
        ("va_kolakaluru", ["kolakaluru", "nandivelugu"]),  # own village + a sibling: reviewed
        ("ma_tenali", ["kolakaluru", "nandivelugu"]),  # both inside the mandal: live
        ("ma_tenali", ["tenali", "kaza"]),  # own mandal + another mandal's village: reviewed
        ("da_guntur", ["tenali", "kaza"]),  # both inside the district: live
        ("da_guntur", ["tenali", "kesarapalli"]),  # + a village in another district: reviewed
        ("super", ["guntur", "kesarapalli"]),  # anywhere: live
    ],
)
def test_one_area_outside_sends_the_whole_post_to_review(api, world, author, targets):
    live = expected_live(author, targets)
    said, status = send(api, world, author, targets)
    assert said is live
    assert status == (PostStatus.PUBLISHED if live else PostStatus.PENDING)


def test_a_post_for_everyone_from_an_admin_is_reviewed_by_the_super_admin(api, world):
    said, status = send(api, world, "da_guntur", ["ap"])
    assert said is False and status == PostStatus.PENDING
    post_id = api(world.users["da_guntur"]).get(POSTS, {"mine": 1}).json()["results"][0]["id"]
    # Another District Admin cannot see or decide it; the Super Admin can.
    assert api(world.users["da_krishna"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"}, format="json").status_code == 404
    decided = api(world.users["super"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"}, format="json")
    assert decided.status_code == 200 and decided.json()["status"] == PostStatus.PUBLISHED


def test_an_outside_post_is_reviewed_by_that_areas_admin(api, world):
    said, status = send(api, world, "va_kolakaluru", ["nandivelugu"])
    assert status == PostStatus.PENDING
    post_id = api(world.users["va_kolakaluru"]).get(POSTS, {"mine": 1}).json()["results"][0]["id"]
    decided = api(world.users["va_nandivelugu"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"}, format="json")
    assert decided.status_code == 200 and decided.json()["status"] == PostStatus.PUBLISHED


def test_a_resubmitted_outside_post_is_reviewed_again(api, world):
    send(api, world, "va_kolakaluru", ["nandivelugu"])
    post_id = api(world.users["va_kolakaluru"]).get(POSTS, {"mine": 1}).json()["results"][0]["id"]
    recheck = api(world.users["va_nandivelugu"]).post(
        f"{POSTS}{post_id}/review/", {"action": "recheck", "comment": "Add the date"}, format="json"
    )
    assert recheck.status_code == 200, recheck.json()
    resubmitted = api(world.users["va_kolakaluru"]).post(f"{POSTS}{post_id}/submit/", format="json")
    assert resubmitted.status_code == 200 and resubmitted.json()["status"] == PostStatus.PENDING


def test_with_auto_publish_off_every_admin_post_is_reviewed(api, world, settings):
    settings.ADMIN_POSTS_AUTO_PUBLISH = False
    said, status = send(api, world, "va_kolakaluru", ["kolakaluru"])
    assert said is False and status == PostStatus.PENDING


# --- The check itself ---------------------------------------------------------------------------------


def test_the_check_needs_areas(api, world):
    response = api(world.users["va_kolakaluru"]).post(CHECK, {"targets": []}, format="json")
    assert response.status_code == 400 and response.json()["error"]["code"] == "validation_error"


def test_the_check_refuses_an_unknown_area_like_a_new_post(api, world):
    response = api(world.users["va_kolakaluru"]).post(CHECK, {"targets": [{"level": "VILLAGE", "id": 999999}]}, format="json")
    assert response.status_code == 400


def test_the_check_needs_a_login(api, world):
    response = api().post(CHECK, {"targets": [world.area_json("kolakaluru")]}, format="json")
    assert response.status_code == 401


def test_the_check_refuses_a_role_that_cannot_post(api, world, super_admin_can_post):
    super_admin_can_post(False)
    response = api(world.users["super"]).post(CHECK, {"targets": [world.area_json("kolakaluru")]}, format="json")
    assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


def test_the_check_changes_nothing(api, world):
    from apps.posts.models import Post

    api(world.users["va_kolakaluru"]).post(CHECK, {"targets": [world.area_json("kolakaluru")]}, format="json")
    assert not Post.objects.exists()

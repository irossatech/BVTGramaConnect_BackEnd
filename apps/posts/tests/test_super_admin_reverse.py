"""A Super Admin may change any decision (owner's decision of 2026-11-21).

Before, a reverse needed a group ranked strictly above the one that decided, and nothing ranks above
the Super Admin, so a Super Admin's decision was final for everyone. Now a Super Admin may reverse every
decided post, another Super Admin's decision and their own included. Everyone else keeps the rank rule.
A comment stays required; each reverse writes a REVERSE ReviewAction and a post.reversed audit entry.
Contract section 10.
"""

import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditLog
from apps.posts.models import PostStatus
from conftest import make_official

pytestmark = pytest.mark.django_db


def review(client, post, action, comment="", **extra):
    return client.post(f"/api/v1/posts/{post.id}/review/", {"action": action, "comment": comment, **extra}, format="json")


@pytest.fixture
def second_super(world):
    return make_official(world, "super_two", Role.SUPER_ADMIN, "ap", "9000000099")


def test_a_super_admin_reverses_their_own_discard(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    client = api(world.users["super"])
    assert review(client, post, "discard", "తప్పు").status_code == 200
    assert "reverse" in client.get(f"/api/v1/posts/{post.id}/").json()["allowed_actions"]
    back = review(client, post, "reverse", "పొరపాటు", to_status="PUBLISHED")
    assert back.status_code == 200 and back.json()["status"] == "PUBLISHED"
    assert post.review_actions.filter(action="REVERSE", from_status="DISCARDED", to_status="PUBLISHED").exists()
    assert AuditLog.objects.filter(action="post.reversed", target_id=str(post.id)).count() == 1


def test_a_super_admin_reverses_another_super_admins_take_down(api, world, make_post, second_super):
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api(world.users["va_kolakaluru"]), post, "publish")
    assert review(api(world.users["super"]), post, "unpublish", "తప్పు సమాచారం").status_code == 200
    back = review(api(second_super), post, "reverse", "సరైనదే", to_status="PUBLISHED")
    assert back.status_code == 200 and back.json()["status"] == "PUBLISHED"


def test_a_comment_is_still_required(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    client = api(world.users["super"])
    review(client, post, "discard", "x")
    assert review(client, post, "reverse", "", to_status="PUBLISHED").status_code == 400


@pytest.mark.parametrize("reviewer", ["da_guntur", "ma_tenali", "va_kolakaluru"])
def test_no_one_else_can_reverse_a_super_admins_decision(api, world, make_post, reviewer):
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api(world.users["super"]), post, "discard", "x")
    client = api(world.users[reviewer])
    assert "reverse" not in client.get(f"/api/v1/posts/{post.id}/").json()["allowed_actions"]
    response = review(client, post, "reverse", "x", to_status="PUBLISHED")
    assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


def test_a_district_admin_still_cannot_reverse_another_district_admins_decision(api, world, make_post):
    """The rank rule is unchanged for everyone but the Super Admin: same rank is not higher."""
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api(world.users["da_guntur"]), post, "discard", "x")
    other = make_official(world, "da_guntur_two", Role.DISTRICT_ADMIN, "guntur", "9000000098")
    response = review(api(other), post, "reverse", "x", to_status="PUBLISHED")
    assert response.status_code == 403


def test_the_super_admin_cannot_reverse_a_post_that_is_not_decided(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    response = review(api(world.users["super"]), post, "reverse", "x", to_status="PUBLISHED")
    assert response.status_code == 409
    post.refresh_from_db()
    assert post.status == PostStatus.PENDING

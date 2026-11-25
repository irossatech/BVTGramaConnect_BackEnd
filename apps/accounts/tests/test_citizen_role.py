"""A citizen's wallet account is never an official (BVTGC-121, design section 5.3).

The Citizen group holds only wallet.use_wallet. The officials' login refuses the account like a wrong
password; the officials' endpoints that need no particular permission answer it 403; the posts an
official sees, the Telugu name suggestions, the user lists and the dashboard counts leave it out.
The matrix of every group x permission is in test_matrix.py.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts import authz
from apps.accounts.models import RoleAssignment, User
from apps.accounts.tokens import issue_tokens
from apps.audit.models import AuditLog
from apps.citizens.models import CitizenAccount
from apps.posts import services as post_services
from apps.posts.models import Category, PostStatus
from conftest import TEST_PASSWORD, make_bare_post

pytestmark = pytest.mark.django_db


def citizen_account(world, village_key: str = "kolakaluru", username: str = "citizen_ravi") -> User:
    user = User.objects.create_user(username=username, name="రవి", password=TEST_PASSWORD)
    now = timezone.now()
    CitizenAccount.objects.create(
        user=user, village_id=world.ref(village_key).id, age_confirmed_at=now, consent_version="v1",
        consent_at=now, referral_code=f"R{username[-7:].upper():0>7}"[:8],
    )
    RoleAssignment.objects.assign(user, "CITIZEN", world.ref(village_key))
    return User.objects.get(pk=user.pk)


@pytest.fixture
def citizen(world):
    return citizen_account(world)


def test_a_citizen_account_holds_only_its_wallet(citizen):
    assert authz.is_citizen_account(citizen)
    assert authz.can(citizen, "wallet.use_wallet")
    assert not authz.can(citizen, "accounts.use_official_api")
    assert authz.official_grants(citizen) == []


def test_an_official_is_not_a_citizen_account(world):
    official = world.users["va_kolakaluru"]
    assert not authz.is_citizen_account(official)
    assert authz.official_grants(official) != []


@pytest.mark.parametrize("url", ["/api/v1/auth/login/", "/api/v1/auth/session/login/"])
def test_the_officials_login_refuses_a_citizen_account_like_a_wrong_password(api, citizen, url):
    response = api().post(url, {"username": citizen.username, "password": TEST_PASSWORD}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_credentials"
    entry = AuditLog.objects.filter(action="auth.login_refused").latest("created_at")
    assert entry.data == {"reason": "citizen_account"}


def test_a_blocked_citizen_account_gets_the_same_answer(api, citizen):
    # Not "account_blocked": the answer must tell nothing about the account.
    User.objects.filter(pk=citizen.pk).update(is_active=False)
    response = api().post("/api/v1/auth/login/", {"username": citizen.username, "password": TEST_PASSWORD}, format="json")
    assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_credentials")


@pytest.mark.parametrize(
    "method, url",
    [
        ("get", "/api/v1/me/"),
        ("post", "/api/v1/auth/password/change/"),
        ("get", "/api/v1/notifications/"),
        ("post", "/api/v1/notifications/read-all/"),
        ("get", "/api/v1/posts/"),
        ("get", "/api/v1/admin/transliterate/?text=ravi"),
    ],
)
def test_officials_endpoints_answer_a_citizen_account_403(api, citizen, method, url):
    response = getattr(api(citizen), method)(url, {}, format="json") if method == "post" else api(citizen).get(url)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.parametrize(
    "url", ["/api/v1/me/", "/api/v1/notifications/", "/api/v1/posts/", "/api/v1/admin/transliterate/?text=ravi"]
)
def test_officials_still_reach_them(api, world, url):
    assert api(world.users["va_kolakaluru"]).get(url).status_code == 200


def test_a_citizen_account_may_log_out(api, citizen):
    tokens = issue_tokens(citizen)
    response = api(citizen).post("/api/v1/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 204


def test_the_posts_an_official_sees_ignore_a_citizen_role(world, citizen):
    village = world.ref("kolakaluru")
    now = timezone.now()
    make_bare_post(
        author=world.users["va_kolakaluru"], posted_as_role="VILLAGE_ADMIN", posted_as_level=village.level,
        posted_as_area_id=village.id, category=Category.objects.get(key="announce"), title="Live", body="Text.",
        target_level=village.level, target_area_id=village.id, status=PostStatus.PUBLISHED,
        submitted_at=now, published_at=now, decided_at=now,
    )
    assert post_services.visible_posts(citizen).count() == 0
    assert post_services.visible_posts(world.users["pm_kolakaluru"]).count() == 1


def test_no_official_lists_or_gives_a_citizen_account(world, citizen):
    for name, official in world.users.items():
        if name == "citizen_ravi":
            continue
        assert citizen not in authz.manageable_users(official), name
        assert "CITIZEN" not in {role.key for role in authz.assignable_roles(official)}, name


def test_the_super_admin_users_list_leaves_out_citizen_accounts(api, world, citizen):
    response = api(world.users["super"]).get("/api/v1/users/?page_size=100")
    assert response.status_code == 200
    assert str(citizen.id) not in {row["id"] for row in response.json()["results"]}


def test_the_dashboard_counts_officials_only(api, world):
    super_admin = api(world.users["super"])
    before = super_admin.get("/api/v1/admin/dashboard/").json()["kpis"]
    citizen_account(world, username="citizen_one")
    blocked = citizen_account(world, username="citizen_two")
    User.objects.filter(pk=blocked.pk).update(is_active=False)
    after = super_admin.get("/api/v1/admin/dashboard/").json()["kpis"]
    assert (after["active_users"], after["blocked_users"]) == (before["active_users"], before["blocked_users"])

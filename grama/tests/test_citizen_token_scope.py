"""A citizen's wallet login opens only the citizen's endpoints (BVTGC-130; requirements FR-5; contract
section 21, "Which calls carry the citizen's token").

The test walks every /api/v1/ route and method of this server. With a citizen account's JWT:
- the public endpoints answer as for anyone (no login needed, a token changes nothing);
- the citizen's own endpoints, the device registration and the token refresh and logout work;
- every other one, the officials' and the Super Admin's, answers 403 permission_denied, before
  looking anything up.

A new route fails this test until it is put in one of the two lists below or refuses citizens, so no
officials' endpoint can open to citizens by mistake.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from apps.accounts.models import RoleAssignment, User
from apps.citizens.models import CitizenAccount
from apps.wallet.models import Wallet

pytestmark = pytest.mark.django_db

#: No login needed: a citizen's token changes nothing there (each has its own tests).
PUBLIC = {
    "health/", "auth/login/", "auth/csrf/", "auth/session/login/", "auth/session/logout/",
    "geo/states/", "geo/districts/", "geo/mandals/", "geo/villages/", "geo/villages/<int:pk>/", "geo/nearest/",
    "categories/", "app-config/", "feed/", "feed/posts/", "feed/upcoming/", "feed/urgent/", "feed/posts/<uuid:pk>/",
    "^feed/share/(?P<code>[A-Za-z0-9]{1,10})/$", "public/site-config/", "public/contact-messages/",
    "public/showcase/", "contacts/", "contact-types/", "services/", "ads/", "bills/",
    # Citizen phones (section 15) use X-Citizen-Token and ignore Authorization.
    "citizen/challenge/", "citizen/register/", "citizen/me/", "citizen/posts/", "citizen/service-requests/",
    "citizen/service-requests/<int:pk>/cancel/",
}
#: Where the app sends the citizen's JWT (contract section 21).
CITIZEN_JWT = {
    "auth/refresh/", "auth/logout/", "devices/", "devices/<uuid:pk>/", "wallet/",
}
CITIZEN_JWT_PREFIXES = ("citizen/auth/", "citizen/account/")


def walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern


def concrete(route: str) -> str:
    """A URL that this route matches, with made-up ids."""
    route = re.sub(r"<uuid:\w+>", str(uuid.uuid4()), route)
    route = re.sub(r"<int:\w+>", "1", route)
    route = re.sub(r"<(?:str|slug|path):\w+>", "x", route)
    route = re.sub(r"\(\?P<\w+>\[0-9\]\+\)", "1", route)
    route = re.sub(r"\(\?P<\w+>\[A-Za-z0-9\]\{1,10\}\)", "abc123", route)
    route = re.sub(r"\(\?P<\w+>[^)]*\)", str(uuid.uuid4()), route)
    return "/" + route.replace("^", "").replace("$", "")


def methods(pattern: URLPattern) -> list[str]:
    actions = getattr(pattern.callback, "actions", None)
    if actions:
        return sorted(actions)
    view = getattr(pattern.callback, "view_class", None) or getattr(pattern.callback, "cls", None)
    return [m for m in ("get", "post", "put", "patch", "delete") if view is not None and hasattr(view, m)]


def api_routes() -> list[tuple[str, str, str]]:
    """(route without api/v1/, method, a URL it matches) for every API route, without format suffixes."""
    found = []
    for route, pattern in walk(get_resolver().url_patterns):
        if not route.startswith("api/v1/") or "(?P<format>" in route:
            continue
        short = route[len("api/v1/"):]
        found += [(short, method, concrete(route)) for method in methods(pattern)]
    return sorted(set(found))


def officials_only(route: str) -> bool:
    return route not in PUBLIC and route not in CITIZEN_JWT and not route.startswith(CITIZEN_JWT_PREFIXES)


OFFICIAL_ROUTES = [(route, method, url) for route, method, url in api_routes() if officials_only(route)]


@pytest.fixture
def citizen_client(api, world, settings):
    settings.WALLET_ENABLED = True
    user = User.objects.create_user(username="citizen.scope", name="Citizen", password="48213957")
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=world.ref("kolakaluru").id, age_confirmed_at=now, consent_version="v1",
        consent_at=now, referral_code="SCOPE001",
    )
    Wallet.objects.create(account=account)
    RoleAssignment.objects.assign(user, "CITIZEN", world.ref("kolakaluru"))
    return api(User.objects.get(pk=user.pk))


def test_the_lists_name_only_routes_that_exist():
    routes = {route for route, _method, _url in api_routes()}
    assert PUBLIC <= routes and CITIZEN_JWT <= routes
    assert len(OFFICIAL_ROUTES) > 90  # the walk really found the officials' endpoints


@pytest.mark.parametrize("route, method, url", OFFICIAL_ROUTES, ids=[f"{m} {r}" for r, m, _u in OFFICIAL_ROUTES])
def test_a_citizen_token_is_refused_on_every_officials_endpoint(citizen_client, route, method, url):
    response = getattr(citizen_client, method)(url, {}, format="json")
    assert response.status_code == 403, (route, method, response.status_code, response.content[:200])
    assert response.json()["error"]["code"] == "permission_denied"


def test_the_citizen_endpoints_still_take_it(citizen_client):
    assert citizen_client.get("/api/v1/wallet/").status_code == 200
    assert citizen_client.get("/api/v1/citizen/account/").status_code == 200
    assert citizen_client.post("/api/v1/devices/", {"fcm_token": "tok-scope", "platform": "android"},
                               format="json").status_code in (200, 201)

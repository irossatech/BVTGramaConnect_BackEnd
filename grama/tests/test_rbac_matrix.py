"""Every endpoint in grama/tests/config/rbac-matrix.json x every role gets the answer the config gives
(audit stage S6, BVTGC-17, the reviewable RBAC matrix).

The config is the review copy: one row per endpoint, naming an expectation profile (a role -> answer
row) taken from docs/api-contract.md, section 11. Each role logs in the way the clients do: officials
and the citizen's wallet account with a real JWT, a citizen's phone with its X-Citizen-Token. The
group x permission x area matrix under every permission check is apps/accounts/tests/test_matrix.py;
this file checks the doors of the endpoints themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.citizens.models import CitizenAccount
from apps.citizens.tests.conftest import register
from apps.wallet.models import Wallet
from conftest import TEST_PASSWORD, client_for
from grama.tests.test_api_contract import PARAM

CONFIG = Path(__file__).resolve().parent / "config" / "rbac-matrix.json"
MATRIX = json.loads(CONFIG.read_text(encoding="utf-8"))
ROLES = list(MATRIX["roles"])
STATUS = {"login": 401, "deny": 403, "hidden": 404}
OFFICIAL_OF = {"panchayat": "pm_kolakaluru", "village_admin": "va_kolakaluru", "mandal_admin": "ma_tenali",
               "district_admin": "da_guntur", "super_admin": "super"}
PLACES = {"{village}": "kolakaluru", "{mandal}": "tenali", "{district}": "guntur", "{state}": "ap"}


def endpoint_id(row: dict) -> str:
    suffix = "" if "wallet" not in row else (" [wallet on]" if row["wallet"] else " [wallet off]")
    return f"{row['method']} {row['path']}{suffix}"


def expected(row: dict) -> dict[str, int]:
    profile = MATRIX["profiles"][row["profile"]]
    return {role: row.get("allow", 200) if profile[role] == "allow" else STATUS[profile[role]] for role in ROLES}


# --- The config itself ------------------------------------------------------------------------------


def test_the_config_is_complete_and_consistent():
    """Every profile names every role with a known answer, and every endpoint names a known profile."""
    known = set(MATRIX["answers"])
    for name, profile in MATRIX["profiles"].items():
        assert set(profile) == set(ROLES), name
        assert set(profile.values()) <= known, name
    seen = set()
    for row in MATRIX["endpoints"]:
        assert row["method"] in ("GET", "POST", "PATCH", "PUT", "DELETE"), row
        assert row["profile"] in MATRIX["profiles"], row
        assert row.get("contract"), f"{endpoint_id(row)}: say where in the contract the rule is"
        assert endpoint_id(row) not in seen, f"listed twice: {endpoint_id(row)}"
        seen.add(endpoint_id(row))


def test_every_endpoint_in_the_config_exists_on_the_server():
    from django.urls import Resolver404, resolve

    for row in MATRIX["endpoints"]:
        try:
            resolve("/api/v1/" + row["path"].split("?")[0])
        except Resolver404:
            pytest.fail(f"not on the server: {endpoint_id(row)}")


# --- The matrix -----------------------------------------------------------------------------------


def citizen_account(world) -> User:
    """A wallet account as sign-up leaves it: the account, its wallet and the Citizen group."""
    user = User.objects.create_user(username="citizen_ravi", name="రవి", password=TEST_PASSWORD)
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=world.ref("kolakaluru").id, age_confirmed_at=now, consent_version="v1",
        consent_at=now, referral_code="RRAVI001",
    )
    Wallet.objects.create(account=account)
    RoleAssignment.objects.assign(user, "CITIZEN", world.ref("kolakaluru"))
    return user


def clients(world) -> dict[str, APIClient]:
    phone = register(world).json()["token"]
    users = {role: world.users[name] for role, name in OFFICIAL_OF.items()}
    return {
        "anonymous": APIClient(),
        "citizen_phone": APIClient(HTTP_X_CITIZEN_TOKEN=phone),
        "citizen_account": client_for(citizen_account(world)),
        **{role: client_for(user) for role, user in users.items()},
    }


def url(world, path: str) -> str:
    for placeholder, key in PLACES.items():
        path = path.replace(placeholder, str(world.ref(key).id))
    return "/api/v1/" + path


@pytest.mark.django_db
@pytest.mark.parametrize("row", MATRIX["endpoints"], ids=endpoint_id)
def test_each_role_gets_the_answer_the_config_gives(world, settings, row):
    settings.WALLET_ENABLED = row.get("wallet", False)
    address = url(world, row["path"])
    got = {}
    for role, client in clients(world).items():
        if row["method"] == "GET":
            response = client.get(address)
        else:
            response = getattr(client, row["method"].lower())(address, {}, format="json")
        got[role] = response.status_code
    want = expected(row)
    wrong = {role: f"got {got[role]}, want {want[role]}" for role in ROLES if got[role] != want[role]}
    assert not wrong, f"{endpoint_id(row)} ({row['contract']}): {wrong}"


# --- Coverage, for the dashboard -----------------------------------------------------------------------


def api_methods() -> set[tuple[str, str]]:
    """(METHOD, route) for every method every API route answers, parameters as {}."""
    from grama.testsuite_plugin import route_methods

    return {pair for pair in route_methods() if pair[1].startswith("api/v1/")}


def test_the_matrix_covers_every_parameter_free_list_of_the_api():
    """Every GET without an id in the path is in the matrix, so a new list cannot skip its door check.
    Endpoints with an id are covered per object in the area tests (outside the area answers 404)."""
    listed = {("GET", PARAM.sub("{}", "api/v1/" + row["path"].split("?")[0])) for row in MATRIX["endpoints"]
              if row["method"] == "GET"}
    lists = {(method, route) for method, route in api_methods() if method == "GET" and "{}" not in route}
    left_out = {"api/v1/feed/posts/"}  # needs ?ids= of real posts; public like the feed (test_feed.py)
    missing = sorted(route for _method, route in lists - listed if route not in left_out)
    assert not missing, f"Add these lists to grama/tests/config/rbac-matrix.json: {missing}"

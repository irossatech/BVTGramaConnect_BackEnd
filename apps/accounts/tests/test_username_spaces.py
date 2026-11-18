"""Usernames with spaces, and an admin changing a user's username (owner's decision of 2026-11-17).

Contract section 3: usernames are 3 to 150 letters, digits, @ . _ + - and single spaces between words;
the server trims, lower-cases and collapses spaces on create, on change and at login. PATCH /users/{id}/
takes `username`: 409 duplicate when someone else has it, audited as user.username_changed.
"""

import pytest
from axes.models import AccessAttempt

from apps.accounts.lockout import login_key
from apps.accounts.models import User, normalize_username
from apps.accounts.roles import Role
from apps.audit.models import AuditLog

from .test_auth import code, console_session, login

pytestmark = pytest.mark.django_db

USERS = "/api/v1/users/"


def create(api, world, username, actor="va_kolakaluru"):
    body = {"name": "రవి కుమార్", "username": username, "role": Role.PANCHAYAT_MEMBER,
            "area": world.area_json("kolakaluru"), "password": "Ravi-Kumar-2026!"}
    return api(world.users[actor]).post(USERS, body, format="json")


def edit(api, world, actor, target, **body):
    return api(world.users[actor]).patch(f"{USERS}{world.users[target].id}/", body, format="json")


@pytest.mark.parametrize("typed, stored", [
    ("ravi kumar", "ravi kumar"),
    ("  Ravi   Kumar ", "ravi kumar"),
    ("Ravi\tKumar", "ravi kumar"),
    ("ravi.kumar", "ravi.kumar"),
])
def test_normalize_username(typed, stored):
    assert normalize_username(typed) == stored


def test_a_username_with_spaces_is_created_and_normalised(api, world):
    response = create(api, world, "  Ravi  Kumar ")
    assert response.status_code == 201, response.json()
    assert response.json()["user"]["username"] == "ravi kumar"
    assert User.objects.filter(username="ravi kumar").exists()


@pytest.mark.parametrize("bad", ["ab", "ravi_kumar!", "రవి", "x" * 151, "ravi / kumar"])
def test_the_username_rule(api, world, bad):
    response = create(api, world, bad)
    assert response.status_code == 400 and "username" in response.json()["error"]["fields"]


def test_login_with_spaces_and_capitals_finds_the_user(api, world):
    assert create(api, world, "ravi kumar").status_code == 201
    User.objects.filter(username="ravi kumar").update(must_change_password=False)
    for typed in ("ravi kumar", " Ravi  Kumar ", "RAVI KUMAR"):
        response = login(typed, "Ravi-Kumar-2026!")
        assert response.status_code == 200, (typed, response.json())
        assert response.json()["user"]["username"] == "ravi kumar"
    assert code(login("ravikumar", "Ravi-Kumar-2026!")) == "invalid_credentials"


def test_console_login_with_spaces(api, world):
    user = User.objects.get(username="super")
    User.objects.filter(pk=user.pk).update(username="state super")
    _client, response = console_session("  State   Super ")
    assert response.status_code == 200, response.json()


def test_the_lockout_counts_a_spaced_name_however_it_is_typed(api, world):
    assert create(api, world, "ravi kumar").status_code == 201
    for typed in ("ravi kumar", "Ravi Kumar", " ravi  kumar", "RAVI KUMAR ", "ravi kumar"):
        login(typed, "wrong")
    locked = login("ravi kumar", "Ravi-Kumar-2026!")
    assert locked.status_code == 429 and code(locked) == "locked_out"
    assert AccessAttempt.objects.get().username == login_key("ravi kumar")


# --- Changing the username (console: Edit details) ------------------------------------------------


def test_an_admin_changes_the_username(api, world):
    response = edit(api, world, "va_kolakaluru", "pm_kolakaluru", username="  PM  Kolakaluru Two ")
    assert response.status_code == 200, response.json()
    assert response.json()["username"] == "pm kolakaluru two"
    entry = AuditLog.objects.get(action="user.username_changed")
    assert entry.data == {"from": "pm_kolakaluru", "to": "pm kolakaluru two"}
    assert entry.actor_id == world.users["va_kolakaluru"].id
    assert not AuditLog.objects.filter(action="user.updated").exists()  # only the username changed


def test_username_and_other_details_together(api, world):
    response = edit(api, world, "va_kolakaluru", "pm_kolakaluru", username="pm two", name="కొత్త పేరు")
    assert response.status_code == 200
    assert AuditLog.objects.get(action="user.username_changed").data["to"] == "pm two"
    assert set(AuditLog.objects.get(action="user.updated").data["changed"]) == {"name"}


def test_the_new_username_signs_in_and_old_tokens_keep_working(api, world):
    client = api(world.users["pm_kolakaluru"])  # a token issued under the old name
    assert edit(api, world, "va_kolakaluru", "pm_kolakaluru", username="pm new name").status_code == 200
    assert client.get("/api/v1/me/").status_code == 200
    assert login("PM New Name").status_code == 200
    assert code(login("pm_kolakaluru")) == "invalid_credentials"


@pytest.mark.parametrize("taken", ["pm_kaza", "PM_KAZA", " pm_kaza "])
def test_a_taken_username_is_refused_in_any_case(api, world, taken):
    response = edit(api, world, "va_kolakaluru", "pm_kolakaluru", username=taken)
    assert response.status_code == 409 and code(response) == "duplicate"
    assert "username" in response.json()["error"]["fields"]
    assert User.objects.get(pk=world.users["pm_kolakaluru"].pk).username == "pm_kolakaluru"


def test_keeping_the_same_username_is_not_a_change(api, world):
    response = edit(api, world, "va_kolakaluru", "pm_kolakaluru", username="PM_Kolakaluru")
    assert response.status_code == 200
    assert not AuditLog.objects.filter(action="user.username_changed").exists()


def test_a_bad_new_username_is_refused(api, world):
    response = edit(api, world, "va_kolakaluru", "pm_kolakaluru", username="a  ")
    assert response.status_code == 400 and "username" in response.json()["error"]["fields"]


@pytest.mark.parametrize("actor, target, status", [
    ("va_kolakaluru", "pm_kolakaluru", 200),   # the village's admin
    ("ma_tenali", "pm_kolakaluru", 200),       # an admin above
    ("super", "da_guntur", 200),
    ("va_kaza", "pm_kolakaluru", 404),         # another village: hidden
    ("da_krishna", "pm_kolakaluru", 404),      # another district
    ("va_kolakaluru", "ma_tenali", 404),       # ranked above the actor
    ("pm_kolakaluru", "pm_nandivelugu", 403),  # a Panchayat member manages nobody
])
def test_who_may_change_a_username(api, world, actor, target, status):
    response = edit(api, world, actor, target, username="renamed user")
    assert response.status_code == status, response.json()
    renamed = User.objects.filter(username="renamed user").exists()
    assert renamed == (status == 200)


def test_nobody_changes_their_own_username_through_me(api, world):
    client = api(world.users["va_kolakaluru"])
    client.patch("/api/v1/me/", {"username": "other name"}, format="json")
    assert User.objects.get(pk=world.users["va_kolakaluru"].pk).username == "va_kolakaluru"

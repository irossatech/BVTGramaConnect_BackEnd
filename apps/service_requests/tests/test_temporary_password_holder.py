"""A new official still on their temporary password keeps the requests of their area.

Their admin made them for that area, so a request from it stays theirs before their first login,
and higher admins may not take it away as if nobody held it.
"""

import pytest

from apps.accounts.models import User
from apps.service_requests.models import ServiceRequest

from .test_service_requests import HIGHER, actions, detail, patch

pytestmark = pytest.mark.django_db


def on_temporary_password(world, name):
    User.objects.filter(pk=world.users[name].pk).update(must_change_password=True)


def test_a_new_village_admin_gets_the_request_and_keeps_it(api, world, request_from):
    on_temporary_password(world, "va_kolakaluru")
    _citizen, request_id = request_from()
    assert ServiceRequest.objects.get(pk=request_id).assigned_to == world.users["va_kolakaluru"]
    for name in HIGHER:
        assert actions(api, world, name, request_id) == [], name  # no "assign": somebody holds it
        response = patch(api, world, name, request_id, assigned_to=str(world.users[name].id))
        assert response.status_code == 403, name
    assert ServiceRequest.objects.get(pk=request_id).assigned_to == world.users["va_kolakaluru"]


def test_after_choosing_a_password_they_act_on_it(api, world, request_from):
    on_temporary_password(world, "va_kolakaluru")
    _citizen, request_id = request_from()
    User.objects.filter(pk=world.users["va_kolakaluru"].pk).update(must_change_password=False)
    assert actions(api, world, "va_kolakaluru", request_id) == ["set_status", "assign"]
    assert patch(api, world, "va_kolakaluru", request_id, status="IN_PROGRESS").status_code == 200


def test_a_request_may_be_passed_to_an_official_on_a_temporary_password(api, world, request_from):
    on_temporary_password(world, "pm_kolakaluru")
    _citizen, request_id = request_from()
    member = world.users["pm_kolakaluru"]
    response = patch(api, world, "va_kolakaluru", request_id, assigned_to=str(member.id))
    assert response.status_code == 200, response.json()
    assert response.json()["assigned_to"]["id"] == str(member.id)


def test_until_then_they_still_cannot_use_the_api(api, world, request_from):
    on_temporary_password(world, "va_kolakaluru")
    _citizen, request_id = request_from()
    response = detail(api, world, "va_kolakaluru", request_id)
    assert response.status_code == 403 and response.json()["error"]["code"] == "password_change_required"


def test_a_blocked_assignee_still_frees_the_request(api, world, request_from):
    on_temporary_password(world, "va_kolakaluru")
    _citizen, request_id = request_from()
    User.objects.filter(pk=world.users["va_kolakaluru"].pk).update(is_active=False)
    for name in HIGHER:
        assert actions(api, world, name, request_id) == ["assign"], name

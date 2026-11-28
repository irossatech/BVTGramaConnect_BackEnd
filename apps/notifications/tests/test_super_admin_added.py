"""S7 of the super-admin plan (2026-11-06): when a Super Admin makes another one, every OTHER active
Super Admin gets an inbox item and a push; the giver and the new one do not."""

import pytest

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.notifications.models import Notification, NotificationKind, NotificationTemplate
from conftest import TEST_PASSWORD, make_official

pytestmark = pytest.mark.django_db


def test_the_other_super_admins_hear_about_a_new_one(api, world, devices, push_outbox, django_capture_on_commit_callbacks):
    witness = make_official(world, "super2", Role.SUPER_ADMIN, "ap", "9111100002")
    witness.preferred_language = "en"
    witness.save()
    blocked = make_official(world, "super3", Role.SUPER_ADMIN, "ap", "9111100003")
    User.objects.filter(pk=blocked.pk).update(is_active=False)
    body = {"name": "Ravi Kumar", "name_te": "రవి కుమార్", "username": "ravi.admin", "role": Role.SUPER_ADMIN,
            "area": world.area_json("ap"), "current_password": TEST_PASSWORD}
    with django_capture_on_commit_callbacks(execute=True):
        response = api(world.users["super"]).post("/api/v1/users/", body, format="json")
    assert response.status_code == 201, response.content
    new = User.objects.get(username="ravi.admin")

    notes = Notification.objects.filter(kind=NotificationKind.ACCOUNT)
    assert list(notes.values_list("user", flat=True)) == [witness.pk]  # not the giver, the new one, or a blocked one
    assert notes.get().title == "super added Ravi Kumar as a Super Admin"
    # No district, mandal or village admin hears about it.
    assert not Notification.objects.exclude(user=witness).exists()


def test_the_push_goes_to_the_other_super_admins_phone(api, world, push_outbox, django_capture_on_commit_callbacks):
    from apps.notifications.models import Device

    witness = make_official(world, "super2", Role.SUPER_ADMIN, "ap", "9111100002")
    Device.objects.create(fcm_token="tok-super2", platform="android", user=witness)
    Device.objects.create(fcm_token="tok-super", platform="android", user=world.users["super"])
    body = {"name": "Ravi", "username": "ravi.admin", "role": Role.SUPER_ADMIN,
            "area": world.area_json("ap"), "current_password": TEST_PASSWORD}
    with django_capture_on_commit_callbacks(execute=True):
        assert api(world.users["super"]).post("/api/v1/users/", body, format="json").status_code == 201
    assert [message.token for message in push_outbox] == ["tok-super2"]
    message = push_outbox[0]
    assert message.data["type"] == NotificationKind.ACCOUNT
    assert message.data["user_id"] == str(User.objects.get(username="ravi.admin").pk)
    # In the witness's language (Telugu by default), with the names only: never a phone or an email.
    assert message.title == "super గారు Ravi గారిని సూపర్ అడ్మిన్‌గా చేర్చారు"
    assert "@" not in message.title and "9111" not in message.title


def test_no_notification_for_other_roles(api, world, django_capture_on_commit_callbacks):
    make_official(world, "super2", Role.SUPER_ADMIN, "ap", "9111100002")
    body = {"name": "Lakshmi", "username": "lakshmi", "role": Role.DISTRICT_ADMIN, "area": world.area_json("krishna")}
    with django_capture_on_commit_callbacks(execute=True):
        assert api(world.users["super"]).post("/api/v1/users/", body, format="json").status_code == 201
    assert not Notification.objects.filter(kind=NotificationKind.ACCOUNT).exists()


def test_the_template_is_seeded_and_editable():
    template = NotificationTemplate.objects.get(key="super_admin_added")
    assert (template.channel, template.recipient, template.editable) == ("push", "super_admins", True)
    assert template.variables == ["actor", "name"]
    assert "{actor}" in template.text_te and "{name}" in template.text_te

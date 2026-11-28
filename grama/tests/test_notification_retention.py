"""Daily housekeeping deletes officials' read notifications after NOTIFICATION_KEEP_DAYS (90) days.

Unread ones, push devices and citizen phones are never deleted (owner decision, audit finding BE-DB-018).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.citizens.models import CitizenDevice
from apps.notifications.models import Device, Notification, NotificationKind
from grama.housekeeping import run_housekeeping

pytestmark = pytest.mark.django_db


def note(user, *, days_ago, read):
    item = Notification.objects.create(user=user, kind=NotificationKind.choices[0][0], title="తనిఖీ")
    created = timezone.now() - timedelta(days=days_ago)
    Notification.objects.filter(pk=item.pk).update(created_at=created, read_at=created if read else None)
    return item.pk


def test_read_notifications_older_than_90_days_go(world):
    user = world.users["va_kolakaluru"]
    old_read = note(user, days_ago=91, read=True)
    old_unread = note(user, days_ago=200, read=False)
    recent_read = note(user, days_ago=89, read=True)

    assert run_housekeeping()["read_notifications"] == 1
    assert set(Notification.objects.values_list("pk", flat=True)) == {old_unread, recent_read}
    assert not Notification.objects.filter(pk=old_read).exists()


def test_the_days_follow_the_setting_and_0_keeps_everything(world, settings):
    user = world.users["va_kolakaluru"]
    note(user, days_ago=40, read=True)
    settings.NOTIFICATION_KEEP_DAYS = 0
    assert run_housekeeping()["read_notifications"] == 0
    settings.NOTIFICATION_KEEP_DAYS = 30
    assert run_housekeeping()["read_notifications"] == 1
    assert Notification.objects.count() == 0


def test_push_devices_and_citizen_phones_are_kept(world):
    user = world.users["va_kolakaluru"]
    Device.objects.create(user=user, fcm_token="old-phone-token", platform=Device._meta.get_field("platform").choices[0][0])
    Device.objects.filter(user=user).update(**{
        field.name: timezone.now() - timedelta(days=1000)
        for field in Device._meta.concrete_fields
        if field.get_internal_type() == "DateTimeField"
    })
    devices, phones = Device.objects.count(), CitizenDevice.objects.count()
    run_housekeeping()
    assert (Device.objects.count(), CitizenDevice.objects.count()) == (devices, phones) and devices == 1

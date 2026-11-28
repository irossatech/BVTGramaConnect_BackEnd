"""SMS was dropped (officials log in with username and password; invites go by email)."""

from django.db import migrations

SMS_KEYS = ["welcome_sms", "otp_sms"]


def remove_sms_templates(apps, schema_editor):
    apps.get_model("notifications", "NotificationTemplate").objects.filter(key__in=SMS_KEYS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0002_seed_templates"),
    ]

    operations = [
        migrations.RunPython(remove_sms_templates, migrations.RunPython.noop),
    ]

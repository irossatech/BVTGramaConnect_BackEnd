from django.db import migrations

# Frozen copy of the default at the time of this migration (super-admin plan of 2026-11-06, S7).
# Later edits to notifications/templates_data.py do not change databases already seeded.
TEMPLATES = [
    ("super_admin_added", "కొత్త సూపర్ అడ్మిన్", "New Super Admin", "push", "super_admins",
     "{actor} గారు {name} గారిని సూపర్ అడ్మిన్‌గా చేర్చారు", "{actor} added {name} as a Super Admin",
     ["actor", "name"], True, 23),
]


def seed(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for key, name_te, name_en, channel, recipient, text_te, text_en, variables, editable, order in TEMPLATES:
        NotificationTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name_te": name_te,
                "name_en": name_en,
                "channel": channel,
                "recipient": recipient,
                "text_te": text_te,
                "text_en": text_en,
                "variables": variables,
                "editable": editable,
                "order": order,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("notifications", "NotificationTemplate").objects.filter(key__in=[row[0] for row in TEMPLATES]).delete()


class Migration(migrations.Migration):
    dependencies = [("notifications", "0011_super_admin_peers")]

    operations = [migrations.RunPython(seed, unseed)]

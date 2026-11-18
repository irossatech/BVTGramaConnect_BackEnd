from django.db import migrations

# Frozen copy of the default at the time of this migration (owner's decision of 2026-11-18: a citizen
# may cancel their own service request; its assignee is told). Later edits to
# notifications/templates_data.py do not change databases already seeded.
TEMPLATES = [
    ("service_request_cancelled", "రద్దు చేసిన సేవా అభ్యర్థన", "Service request cancelled", "push", "assignee",
     "{name} తమ {service} అభ్యర్థనను రద్దు చేశారు", "{name} cancelled their {service} request",
     ["area", "service", "name"], True, 22),
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
    dependencies = [("notifications", "0012_seed_super_admin_template")]

    operations = [migrations.RunPython(seed, unseed)]

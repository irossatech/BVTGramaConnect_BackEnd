from django.db import migrations

# Frozen copy of the defaults at the time of this migration (docs/api-contract.md, section 16).
# Later edits to notifications/templates_data.py do not change databases already seeded.
TEMPLATES = [
    ("service_request_new", "కొత్త సేవా అభ్యర్థన", "New service request", "push", "reviewers",
     "{area}: {name} నుండి కొత్త {service} అభ్యర్థన", "{area}: new {service} request from {name}",
     ["area", "service", "name"], True, 20),
    ("service_request_updated", "సేవా అభ్యర్థన స్థితి", "Service request status", "push", "citizen",
     "మీ {service} అభ్యర్థన: {status}", "Your {service} request: {status}",
     ["service", "status", "note"], True, 21),
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
    dependencies = [("notifications", "0006_service_requests")]

    operations = [migrations.RunPython(seed, unseed)]

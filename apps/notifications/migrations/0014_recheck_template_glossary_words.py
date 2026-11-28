from django.db import migrations

# BVTGC-67: the recheck push uses the glossary's word for RECHECK (the app's "మళ్ళీ తనిఖీ"), as the
# app's own notification line does. Only a field still holding the old default changes, so a text the
# Super Admin edited in the console stays as they wrote it. Frozen values: later edits to
# notifications/templates_data.py do not change this migration.
KEY = "post_recheck"
OLD = {
    "name_te": "సవరణ అవసరం",
    "text_te": "మీ పోస్ట్‌ను సరిచేయాలి: {comment}",
    "text_en": "Your post needs changes: {comment}",
}
NEW = {
    "name_te": "మళ్ళీ తనిఖీ",
    "text_te": "మీ పోస్ట్ మళ్ళీ తనిఖీకి వచ్చింది: {comment}",
    "text_en": "Your post needs a recheck: {comment}",
}


def _swap(apps, before, after):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for field, old in before.items():
        NotificationTemplate.objects.filter(key=KEY, **{field: old}).update(**{field: after[field]})


def forward(apps, schema_editor):
    _swap(apps, OLD, NEW)


def backward(apps, schema_editor):
    _swap(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [("notifications", "0013_seed_service_request_cancelled_template")]

    operations = [migrations.RunPython(forward, backward)]

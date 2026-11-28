"""The four categories from the prototypes. Their keys drive the design-system colours."""

from django.db import migrations

CATEGORIES = [
    ("announce", "గ్రామ చాటింపులు", "Announcements", "చాటింపులు", "Announcements", 1),
    ("schemes", "ప్రభుత్వ పథకాలు", "Govt Schemes", "పథకాలు", "Schemes", 2),
    ("news", "వార్తలు & నోటీసులు", "News & Notices", "వార్తలు", "News", 3),
    ("updates", "ప్రజా అప్‌డేట్లు", "Public Updates", "అప్‌డేట్లు", "Updates", 4),
]


def seed(apps, schema_editor):
    Category = apps.get_model("posts", "Category")
    for key, name_te, name_en, short_te, short_en, order in CATEGORIES:
        Category.objects.update_or_create(
            key=key,
            defaults={
                "name_te": name_te,
                "name_en": name_en,
                "short_name_te": short_te,
                "short_name_en": short_en,
                "order": order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("posts", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

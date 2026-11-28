"""Seed the contact types. Keys are the mobile app's icon keys, so the app needs no mapping."""

from django.db import migrations

AREA, HELPLINE = "AREA", "HELPLINE"

# (section, key, name_en, name_te, is_other)
TYPES = [
    (AREA, "vro", "VRO", "వీఆర్వో", False),
    (AREA, "meeseva", "Mee Seva", "మీ సేవ", False),
    (AREA, "sachivalayam", "Sachivalayam", "సచివాలయం", False),
    (AREA, "revenue", "Revenue office", "రెవెన్యూ కార్యాలయం", False),
    (AREA, "official", "Govt. official", "ప్రభుత్వ అధికారి", False),
    (AREA, "other", "Other", "ఇతర", True),
    (HELPLINE, "emergency", "Emergency", "అత్యవసరం", False),
    (HELPLINE, "farmers", "Farmers", "రైతులు", False),
    (HELPLINE, "senior", "Senior Citizens", "వృద్ధులు", False),
    (HELPLINE, "student", "Students", "విద్యార్థులు", False),
    (HELPLINE, "women", "Women & Child", "మహిళలు & పిల్లలు", False),
    (HELPLINE, "health", "Health", "ఆరోగ్యం", False),
    (HELPLINE, "police", "Police", "పోలీస్", False),
    (HELPLINE, "railways", "Railways", "రైల్వే", False),
    (HELPLINE, "power", "Electricity", "విద్యుత్", False),
    (HELPLINE, "grievance", "Grievances", "ఫిర్యాదులు", False),
    (HELPLINE, "more", "Other", "ఇతర", True),
]


def forwards(apps, schema_editor):
    ContactType = apps.get_model("contacts", "ContactType")
    db = schema_editor.connection.alias
    for index, (section, key, name_en, name_te, is_other) in enumerate(TYPES):
        ContactType.objects.using(db).update_or_create(
            key=key,
            defaults={
                "section": section,
                "name_en": name_en,
                "name_te": name_te,
                "is_other": is_other,
                "sort_order": (index + 1) * 10,
            },
        )


def backwards(apps, schema_editor):
    ContactType = apps.get_model("contacts", "ContactType")
    ContactType.objects.using(schema_editor.connection.alias).filter(key__in=[t[1] for t in TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [("contacts", "0001_initial")]

    operations = [migrations.RunPython(forwards, backwards)]

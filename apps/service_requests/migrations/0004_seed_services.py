from django.db import migrations

# The services the app ships with (docs/api-contract.md, section 16), with the same keys.
# Frozen here: later changes are made by the Super Admin in the app or the console.
SERVICES = [
    ("aadhaar", "Aadhaar Services", "ఆధార్ సేవలు", "aadhaar", "orange"),
    ("pan", "PAN Services", "పాన్ సేవలు", "id_badge", "blue"),
    ("voter_id", "Voter ID", "ఓటర్ ఐడి", "eci", "green"),
    ("certificates", "Certificates", "ధ్రువపత్రాలు", "certificate", "purple"),
    ("loans_insurance", "Loans & Insurance", "రుణాలు & బీమా", "loan", "teal"),
    ("govt_schemes", "Govt Schemes", "ప్రభుత్వ పథకాలు", "scheme", "orange"),
    ("land_records", "Land Records", "భూ రికార్డులు", "land", "purple"),
    ("farmer", "Farmer Services", "రైతు సేవలు", "farmer", "green"),
]


def seed(apps, schema_editor):
    Service = apps.get_model("service_requests", "Service")
    for order, (key, name_en, name_te, icon, color) in enumerate(SERVICES):
        Service.objects.get_or_create(
            key=key, defaults={"name_en": name_en, "name_te": name_te, "icon": icon, "color": color, "order": order}
        )


def unseed(apps, schema_editor):
    apps.get_model("service_requests", "Service").objects.filter(key__in=[row[0] for row in SERVICES]).delete()


class Migration(migrations.Migration):
    dependencies = [("service_requests", "0003_services_and_requests")]

    operations = [migrations.RunPython(seed, unseed)]

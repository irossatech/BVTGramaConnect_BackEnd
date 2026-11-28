from django.db import migrations

# The five bills the app used to ship (docs/api-contract.md, section 18), so Home looks the same
# on day one. Frozen here: later changes are made by the Super Admin in the app or the console.
BILLS = [
    ("electricity", "Electricity Bill", "కరెంట్ బిల్లు", "power", "orange"),
    ("water_bill", "Water Bill", "నీటి బిల్లు", "water", "blue"),
    ("mobile_recharge", "Mobile Recharge", "మొబైల్ రీఛార్జ్", "mobile", "purple"),
    ("dth_recharge", "DTH Recharge", "DTH రీఛార్జ్", "dth", "teal"),
    ("broadband", "Broadband", "బ్రాడ్‌బ్యాండ్", "broadband", "green"),
]


def seed(apps, schema_editor):
    """Only the keys that do not exist yet: safe to run again."""
    Bill = apps.get_model("bills", "Bill")
    for order, (key, name_en, name_te, icon, color) in enumerate(BILLS):
        Bill.objects.get_or_create(
            key=key, defaults={"name_en": name_en, "name_te": name_te, "icon": icon, "color": color, "order": order}
        )


def unseed(apps, schema_editor):
    apps.get_model("bills", "Bill").objects.filter(key__in=[row[0] for row in BILLS]).delete()


class Migration(migrations.Migration):
    dependencies = [("bills", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]

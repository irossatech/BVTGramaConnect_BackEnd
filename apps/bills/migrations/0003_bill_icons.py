from django.db import migrations, models

# Icons of the first list that the app and the console never drew, and what a bill using one
# shows instead. Frozen here. The seeded bills use icons that stay (power, water, mobile, dth, broadband).
RETIRED = {"card": "credit_card", "house": "bill", "help": "bill", "services": "bill"}


def move_retired_icons(apps, schema_editor):
    Bill = apps.get_model("bills", "Bill")
    for old, new in RETIRED.items():
        Bill.objects.filter(icon=old).update(icon=new)


class Migration(migrations.Migration):

    dependencies = [
        ('bills', '0002_seed_bills'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bill',
            name='icon',
            field=models.CharField(choices=[('power', 'power'), ('water', 'water'), ('gas', 'gas'), ('mobile', 'mobile'), ('landline', 'landline'), ('dth', 'dth'), ('broadband', 'broadband'), ('fastag', 'fastag'), ('credit_card', 'credit_card'), ('bill', 'bill')], default='bill', max_length=30),
        ),
        migrations.RunPython(move_retired_icons, migrations.RunPython.noop),
    ]

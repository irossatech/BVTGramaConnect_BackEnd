from django.db import migrations

# Frozen copy of the defaults at the time of this migration (BVTGC-129: the citizen wallet's pushes,
# contract section 21). Later edits to notifications/templates_data.py do not change databases already seeded.
TEMPLATES = [
    ("referral_joined", "స్నేహితుడు చేరారు", "A friend joined", "push", "wallet",
     "{name} మీ ఆహ్వాన కోడ్‌తో గ్రామ కనెక్ట్‌లో చేరారు. మీ వాలెట్ చూడండి.",
     "{name} joined Grama Connect with your invite code. See your wallet.", ["name"], True, 30),
    ("points_confirmed", "పాయింట్లు నిర్ధారించబడ్డాయి", "Points confirmed", "push", "wallet",
     "మీ వాలెట్‌లో {points} పాయింట్లు నిర్ధారించబడ్డాయి.", "{points} points are confirmed in your wallet.",
     ["points"], True, 31),
    ("points_not_confirmed", "పాయింట్లు నిర్ధారించబడలేదు", "Points not confirmed", "push", "wallet",
     "{points} పాయింట్లు నిర్ధారించబడలేదు. కారణం మీ వాలెట్‌లో చూడండి.",
     "{points} points were not confirmed. See the reason in your wallet.", ["points", "reason"], True, 32),
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
    dependencies = [("notifications", "0015_wallet_kind_and_recipient")]

    operations = [migrations.RunPython(seed, unseed)]

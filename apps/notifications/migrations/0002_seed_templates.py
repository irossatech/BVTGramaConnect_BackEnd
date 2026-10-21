from django.db import migrations

# Frozen copy of the defaults at the time of this migration. Later edits to
# notifications/templates_data.py do not change databases already seeded.
TEMPLATES = [
    ("post_published", "కొత్త పోస్ట్ ప్రచురణ", "New post for citizens", "push", "citizens",
     "{area}: కొత్త {category}", "{area}: new {category}", ["area", "category", "title"], True),
    ("post_approved", "పోస్ట్ ప్రచురించబడింది", "Post published", "push", "author",
     'మీ పోస్ట్ "{title}" ప్రచురించబడింది.', 'Your post "{title}" has been published.', ["title"], True),
    ("post_recheck", "సవరణ అవసరం", "Recheck needed", "push", "author",
     "మీ పోస్ట్‌ను సరిచేయాలి: {comment}", "Your post needs changes: {comment}", ["title", "comment"], True),
    ("post_discarded", "పోస్ట్ తిరస్కరణ", "Post discarded", "push", "author",
     "మీ పోస్ట్ తిరస్కరించబడింది. కారణం: {comment}", "Your post was discarded. Reason: {comment}",
     ["title", "comment"], True),
    ("post_unpublished", "పోస్ట్ తొలగింపు", "Post taken down", "push", "author",
     'మీ పోస్ట్ "{title}" తొలగించబడింది. కారణం: {comment}', 'Your post "{title}" was taken down. Reason: {comment}',
     ["title", "comment"], True),
    ("review_pending", "సమీక్ష కోసం కొత్త పోస్ట్", "New post to review", "push", "reviewers",
     "{area}లో కొత్త పోస్ట్ సమీక్ష కోసం వేచి ఉంది: {title}", "New post waiting for review in {area}: {title}",
     ["area", "title"], True),
    ("escalation", "ఎస్కలేషన్ హెచ్చరిక", "Escalation alert", "push", "higher_admin",
     "{area}లో పోస్ట్ {hours} గంటలుగా సమీక్ష కోసం వేచి ఉంది.", "A post in {area} has waited {hours} h for review.",
     ["area", "hours", "title"], True),
    ("welcome_sms", "స్వాగత SMS", "Welcome SMS", "sms", "new_user",
     "BVT Grama Connect కు స్వాగతం, {name}. మిమ్మల్ని {area} {role}గా చేర్చారు. ఈ మొబైల్ నంబర్‌తో యాప్‌లో లాగిన్ అవ్వండి.",
     "Welcome to BVT Grama Connect, {name}. You have been added as {role} for {area}. "
     "Log in to the app with this mobile number.",
     ["name", "role", "area"], False),
    ("otp_sms", "OTP లాగిన్", "OTP login", "sms", "official",
     "{otp} మీ BVT Grama Connect లాగిన్ OTP. ఇది {minutes} నిమిషాల్లో ముగుస్తుంది. ఎవరితోనూ పంచుకోవద్దు.",
     "{otp} is your BVT Grama Connect login OTP. It expires in {minutes} minutes. Do not share it with anyone.",
     ["otp", "minutes"], False),
]


def seed(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for order, (key, name_te, name_en, channel, recipient, text_te, text_en, variables, editable) in enumerate(TEMPLATES):
        NotificationTemplate.objects.update_or_create(
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
    apps.get_model("notifications", "NotificationTemplate").objects.filter(
        key__in=[row[0] for row in TEMPLATES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]

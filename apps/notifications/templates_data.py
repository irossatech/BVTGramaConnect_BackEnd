"""Default notification texts. Seeded into NotificationTemplate by a migration
and used as the fallback when a row is missing.

Push texts come from the mobile prototype's Settings screen where it had them.
There are no SMS texts: nothing is sent by SMS, and login is by username and password.
"""

DEFAULTS = [
    {
        "key": "post_published",
        "name_te": "కొత్త పోస్ట్ ప్రచురణ",
        "name_en": "New post for citizens",
        "channel": "push",
        "recipient": "citizens",
        "text_te": "{area}: కొత్త {category}",
        "text_en": "{area}: new {category}",
        "variables": ["area", "category", "title"],
        "editable": True,
    },
    {
        "key": "post_approved",
        "name_te": "పోస్ట్ ప్రచురించబడింది",
        "name_en": "Post published",
        "channel": "push",
        "recipient": "author",
        "text_te": 'మీ పోస్ట్ "{title}" ప్రచురించబడింది.',
        "text_en": 'Your post "{title}" has been published.',
        "variables": ["title"],
        "editable": True,
    },
    {
        "key": "post_recheck",
        "name_te": "సవరణ అవసరం",
        "name_en": "Recheck needed",
        "channel": "push",
        "recipient": "author",
        "text_te": "మీ పోస్ట్‌ను సరిచేయాలి: {comment}",
        "text_en": "Your post needs changes: {comment}",
        "variables": ["title", "comment"],
        "editable": True,
    },
    {
        "key": "post_discarded",
        "name_te": "పోస్ట్ తిరస్కరణ",
        "name_en": "Post discarded",
        "channel": "push",
        "recipient": "author",
        "text_te": "మీ పోస్ట్ తిరస్కరించబడింది. కారణం: {comment}",
        "text_en": "Your post was discarded. Reason: {comment}",
        "variables": ["title", "comment"],
        "editable": True,
    },
    {
        "key": "post_unpublished",
        "name_te": "పోస్ట్ తొలగింపు",
        "name_en": "Post taken down",
        "channel": "push",
        "recipient": "author",
        "text_te": 'మీ పోస్ట్ "{title}" తొలగించబడింది. కారణం: {comment}',
        "text_en": 'Your post "{title}" was taken down. Reason: {comment}',
        "variables": ["title", "comment"],
        "editable": True,
    },
    {
        "key": "review_pending",
        "name_te": "సమీక్ష కోసం కొత్త పోస్ట్",
        "name_en": "New post to review",
        "channel": "push",
        "recipient": "reviewers",
        "text_te": "{area}లో కొత్త పోస్ట్ సమీక్ష కోసం వేచి ఉంది: {title}",
        "text_en": "New post waiting for review in {area}: {title}",
        "variables": ["area", "title"],
        "editable": True,
    },
    {
        "key": "escalation",
        "name_te": "ఎస్కలేషన్ హెచ్చరిక",
        "name_en": "Escalation alert",
        "channel": "push",
        "recipient": "higher_admin",
        "text_te": "{area}లో పోస్ట్ {hours} గంటలుగా సమీక్ష కోసం వేచి ఉంది.",
        "text_en": "A post in {area} has waited {hours} h for review.",
        "variables": ["area", "hours", "title"],
        "editable": True,
    },
]

BY_KEY = {item["key"]: item for item in DEFAULTS}

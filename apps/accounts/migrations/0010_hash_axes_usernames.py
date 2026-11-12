"""Replace the typed usernames already in django-axes' tables with their keyed hash.

From now on lockout.login_key() is what axes stores (AXES_USERNAME_CALLABLE). Rows written before
that hold the typed text, which may be a password typed into the username box. This rewrites
them with the same hash, so current lockouts keep working, and nothing typed stays behind.

The hash is written out here rather than imported, so the migration never changes with the code.
It cannot be undone: the typed text is gone.
"""

import hashlib
import hmac
import re

from django.conf import settings
from django.db import migrations

HASHED = re.compile(r"^[0-9a-f]{64}$")


def _key(username: str) -> str:
    value = (username or "").strip().lower()[:150]
    if not value:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), b"axes-username:" + value.encode(), hashlib.sha256).hexdigest()


def hash_usernames(apps, schema_editor):
    AccessAttempt = apps.get_model("axes", "AccessAttempt")
    AccessFailureLog = apps.get_model("axes", "AccessFailureLog")

    for row in AccessFailureLog.objects.exclude(username__isnull=True).exclude(username="").iterator():
        if not HASHED.match(row.username):
            AccessFailureLog.objects.filter(pk=row.pk).update(username=_key(row.username))

    for row in AccessAttempt.objects.exclude(username__isnull=True).exclude(username="").iterator():
        if HASHED.match(row.username):
            continue
        key = _key(row.username)
        # The unique (username, ip_address, user_agent): if the hashed twin already exists, keep that one.
        twin = AccessAttempt.objects.filter(username=key, ip_address=row.ip_address, user_agent=row.user_agent)
        if twin.exists():
            AccessAttempt.objects.filter(pk=row.pk).delete()
        else:
            AccessAttempt.objects.filter(pk=row.pk).update(username=key)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_super_admin_peers"),
        ("axes", "0010_accessattemptexpiration"),
    ]

    operations = [migrations.RunPython(hash_usernames, migrations.RunPython.noop)]

"""System checks for citizen posting.

Not tagged "security" on purpose: the deploy scripts stop on any security warning
(`check --deploy --tag security --fail-level WARNING`), and Play Integrity being off is the owner's
decision until the app is on Google Play. A plain `manage.py check` (every deploy) prints it.
"""

from django.conf import settings
from django.core import checks


@checks.register()
def integrity_off_on_server(app_configs, **kwargs):
    if settings.DEBUG or settings.CITIZEN_INTEGRITY_REQUIRED:
        return []
    return [
        checks.Warning(
            "Play Integrity is off (CITIZEN_INTEGRITY_REQUIRED=False): any app copy can register to post.",
            hint=(
                "Expected until the app is on Google Play (owner's decision of 2026-11-04) and on the qa "
                "server. On that day write CITIZEN_INTEGRITY_REQUIRED=True in .env and add Play's "
                "app-signing key to ANDROID_CERT_SHA256."
            ),
            id="citizens.W001",
        )
    ]

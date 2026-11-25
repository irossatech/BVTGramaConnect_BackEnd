"""System checks for citizen posting and citizen wallet emails.

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


@checks.register()
def email_not_set_up_on_server(app_configs, **kwargs):
    """Citizens' wallet codes go by email (BVTGC-122); a server without the client's service cannot send them."""
    smtp = settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
    if settings.DEBUG or not smtp or settings.EMAIL_HOST not in ("", "localhost", "127.0.0.1"):
        return []
    return [
        checks.Warning(
            "Email is not set up (EMAIL_HOST is empty or localhost): citizen wallet codes cannot be sent.",
            hint=(
                "Write the client's email service in .env: EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, "
                "EMAIL_HOST_PASSWORD, EMAIL_USE_TLS and DEFAULT_FROM_EMAIL (BVTGC-143). Until then keep the "
                "wallet switched off."
            ),
            id="citizens.W002",
        )
    ]

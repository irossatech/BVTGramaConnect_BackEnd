from django.conf import settings
from django.core.checks import Error, Tags, register

DEV_EMAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
}


@register(Tags.security, deploy=True)
def email_backend_is_real(app_configs, **kwargs):
    """`manage.py check --deploy` fails while invite emails, which carry passwords, would not be sent."""
    if settings.EMAIL_BACKEND in DEV_EMAIL_BACKENDS:
        return [
            Error(
                f"EMAIL_BACKEND is {settings.EMAIL_BACKEND}, which never delivers mail and may print passwords.",
                hint="Set EMAIL_BACKEND to django.core.mail.backends.smtp.EmailBackend and the EMAIL_* variables.",
                id="accounts.E001",
            )
        ]
    return []

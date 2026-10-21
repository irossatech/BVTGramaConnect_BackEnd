"""Background jobs for accounts, run by django-q2 (`manage.py qcluster`).

The invite and reset emails carry the temporary password. It is never put in
the job's arguments, the logs or the audit log: the job reads the role's
default password when it runs and sends it only if it still unlocks the
account (the person has not changed it and the default has not moved on).
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django_q.tasks import async_task

from apps.audit.services import record
from apps.geography.areas import AreaDescriber
from grama.retry import with_retries

from .credentials import highest_role, mask_email, role_default
from .models import User
from .roles import Role, role_label

logger = logging.getLogger("grama.email")

TEXTS = {
    "te": {
        "subject_invite": "మీ BVT Grama Connect లాగిన్ వివరాలు",
        "subject_reset": "మీ BVT Grama Connect పాస్‌వర్డ్ రీసెట్ అయింది",
        "intro_invite": "మిమ్మల్ని BVT Grama Connectలో {area} {role}గా చేర్చారు.",
        "intro_reset": "మీ BVT Grama Connect పాస్‌వర్డ్‌ను రీసెట్ చేశారు.",
        "hello": "నమస్కారం {name},",
        "username": "యూజర్‌నేమ్: {username}",
        "password": "తాత్కాలిక పాస్‌వర్డ్: {password}",
        "where_app": "BVT Grama Connect యాప్ తెరిచి “అధికారుల లాగిన్” ఎంచుకుని ఈ వివరాలు నమోదు చేయండి.",
        "where_console": "సూపర్ అడ్మిన్ కన్సోల్‌లో లాగిన్ అవ్వండి: {url}",
        "change": "మొదటిసారి లాగిన్ అయిన తర్వాత ఈ పాస్‌వర్డ్‌ను మార్చుకోండి. దీన్ని ఎవరితోనూ పంచుకోవద్దు.",
    },
    "en": {
        "subject_invite": "Your BVT Grama Connect login",
        "subject_reset": "Your BVT Grama Connect password was reset",
        "intro_invite": "You have been added to BVT Grama Connect as {role} for {area}.",
        "intro_reset": "Your BVT Grama Connect password has been reset.",
        "hello": "Hello {name},",
        "username": "Username: {username}",
        "password": "Temporary password: {password}",
        "where_app": "Open the BVT Grama Connect app, choose “Official Login” and enter these details.",
        "where_console": "Log in to the Super Admin console: {url}",
        "change": "Please change this password after you first log in. Do not share it with anyone.",
    },
}


def compose_credentials_email(user: User, role: str, password: str, kind: str) -> tuple[str, str]:
    language = "te" if user.preferred_language == "te" else "en"
    text = TEXTS[language]
    assignment = next((ra for ra in user.role_assignments.all() if ra.role == role), None)
    area = AreaDescriber(language).describe(assignment.area) if assignment else None
    name = user.name_te if language == "te" and user.name_te else user.name
    where = (
        text["where_console"].format(url=settings.CONSOLE_LOGIN_URL)
        if role == Role.SUPER_ADMIN
        else text["where_app"]
    )
    lines = [
        text["hello"].format(name=name),
        "",
        text[f"intro_{kind}"].format(role=role_label(role, language), area=area["name"] if area else ""),
        "",
        text["username"].format(username=user.username),
        text["password"].format(password=password),
        "",
        where,
        text["change"],
        "",
        "— BVT Grama Connect",
    ]
    return text[f"subject_{kind}"], "\n".join(lines)


def send_credentials_email(user_id: str, kind: str = "invite") -> bool:
    """Email the username and temporary password. Retries with backoff; audits a final failure."""
    user = User.objects.filter(pk=user_id).prefetch_related("role_assignments").first()
    if user is None or user.is_blocked or not user.email:
        return False
    role = highest_role(user)
    if role is None:
        return False
    password = role_default(role)
    if not user.check_password(password):
        record(
            "email.skipped", target=user, target_label=user.name, via="system",
            data={"kind": kind, "to": mask_email(user.email), "reason": "password_no_longer_the_default"},
        )
        return False

    subject, body = compose_credentials_email(user, role, password, kind)
    attempts = settings.DELIVERY_RETRY_ATTEMPTS
    try:
        with_retries(
            lambda: send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False),
            attempts=attempts,
        )
    except Exception as exc:
        logger.error("Credentials email (%s) to %s failed after %d tries: %s", kind, mask_email(user.email), attempts, type(exc).__name__)
        record(
            "email.failed", target=user, target_label=user.name, via="system",
            data={"kind": kind, "to": mask_email(user.email), "attempts": attempts, "error": type(exc).__name__},
        )
        return False
    return True


def queue_credentials_email(user_id, kind: str = "invite") -> None:
    async_task("apps.accounts.tasks.send_credentials_email", str(user_id), kind, group="email")

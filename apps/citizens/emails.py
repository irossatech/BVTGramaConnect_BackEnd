"""Emails to citizens' wallet accounts (BVTGC-122): codes and account notices, in Telugu or English.

The only email this server sends. Each kind has one text template per language in
templates/citizens/email/<kind>_<language>.txt; its first line is "Subject: ...", the rest the body.
The HTML copy is the same paragraphs in a simple branded layout (layout.html), the code in a box.

Sent in the request through Django's EMAIL_BACKEND (the client's SMTP service on servers, the terminal
on laptops, an outbox in tests), never in a background job: the code exists only in memory, and full
email addresses must not be in task arguments (.claude/conventions.md). Logs name the kind of email
and the error's type, never the address or the code.
"""

from __future__ import annotations

import logging
import smtplib
from email.utils import make_msgid, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit.masking import mask_email

logger = logging.getLogger(__name__)

LANGUAGES = ("te", "en")
KINDS = ("code", "password_changed", "email_changed", "account_deleted")


class EmailUnavailable(Exception):
    """The email service could not take the message. The caller answers 503 email_unavailable."""


def _language(language: str | None) -> str:
    return language if language in LANGUAGES else "te"


def _render(kind: str, language: str, context: dict) -> tuple[str, str, str]:
    if kind not in KINDS:
        raise ValueError(f"unknown email kind {kind!r}")
    language = _language(language)
    raw = render_to_string(f"citizens/email/{kind}_{language}.txt", context).strip()
    first, _newline, body = raw.partition("\n")
    subject = first.removeprefix("Subject:").strip()
    body = body.strip()
    paragraphs = [" ".join(part.split()) for part in body.split("\n\n") if part.strip()]
    html = render_to_string(
        "citizens/email/layout.html",
        {"language": language, "subject": subject, "paragraphs": paragraphs, "code": context.get("code", "")},
    )
    return subject, body + "\n", html


def _send(to: str, kind: str, language: str, context: dict) -> str:
    """Send one email; return its Message-ID. Raise EmailUnavailable when the service refuses or is down."""
    subject, text, html = _render(kind, language, context)
    domain = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition("@")[2] or "localhost"
    message_id = make_msgid(domain=domain)
    message = EmailMultiAlternatives(
        subject, text, settings.DEFAULT_FROM_EMAIL, [to], headers={"Message-ID": message_id}
    )
    message.attach_alternative(html, "text/html")
    try:
        message.send(fail_silently=False)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        # Only the type: SMTP errors can quote the recipient's address.
        logger.warning("Citizen email %r not sent: %s", kind, type(exc).__name__)
        raise EmailUnavailable(kind) from None
    return message_id


def send_code(to: str, *, purpose: str, code: str, language: str, name: str = "", minutes: int = 10) -> str:
    """The code email (sign-up, password reset, a new email, deletion). Raises EmailUnavailable."""
    return _send(to, "code", language, {"purpose": purpose, "code": code, "name": name, "minutes": minutes})


def notify(kind: str, to: str, *, language: str, name: str = "", new_email: str = "") -> bool:
    """A notice without a code: password_changed, email_changed (sent to the old address, naming the
    new one masked) or account_deleted. It never fails the action it reports: a notice that cannot be
    sent is logged and skipped. Returns whether it went out."""
    context = {
        "name": name,
        "when": timezone.localtime().strftime("%d-%m-%Y %H:%M"),
        "new_email_hint": mask_email(new_email) if new_email else "",
    }
    try:
        _send(to, kind, language, context)
    except EmailUnavailable:
        return False
    return True

"""Email codes and notices for citizens' wallet accounts (BVTGC-122; requirements FR-12, FR-32).

Codes: 6 digits, 10 minutes, 5 wrong tries, one a minute, 5 a day per account or address, 10 a day per
phone; kept only as a keyed hash; never in an audit entry or a log line, nor the full address. Emails:
Telugu by default, English on request, a text body and an HTML copy; a notice never fails its action.
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
from datetime import timedelta

import pytest
from django.core import checks
from django.core.mail import EmailMultiAlternatives
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.citizens import codes, emails
from apps.citizens.checks import email_not_set_up_on_server
from apps.citizens.models import CitizenAccount, PendingSignup, VerificationCode
from grama.exceptions import ApiError

pytestmark = pytest.mark.django_db
P = VerificationCode.Purpose
PHONE = "p" * 64


def make_account(n: int = 1, email: str = "ravi@gmail.com") -> CitizenAccount:
    user = User.objects.create_user(username=f"citizen{n}", name="రవి", password="x")
    now = timezone.now()
    return CitizenAccount.objects.create(
        user=user, village_id=1, age_confirmed_at=now, consent_version="v1", consent_at=now,
        referral_code=f"CODE{n:04d}", email=email, email_key=email,
    )


def make_pending(email: str = "lakshmi@gmail.com") -> PendingSignup:
    return PendingSignup.objects.create(
        name="లక్ష్మి", village_id=1, mobile_hash="m" * 64, mobile_last4="3210", email=email, email_key=email,
        password="hash", age_confirmed_at=timezone.now(), consent_version="v1",
        expires_at=timezone.now() + timedelta(hours=24),
    )


def send(owner, *, purpose=P.SIGNUP, email=None, language="en", phone=""):
    email = email or (owner.email if hasattr(owner, "email") else "")
    kind = {"pending_signup": owner} if isinstance(owner, PendingSignup) else {"account": owner}
    return codes.send_code(
        purpose=purpose, to_email=email, email_key=email, language=language, name="లక్ష్మి",
        android_id_hash=phone, **kind,
    )


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def other_than(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def age(seconds: int) -> None:
    """Move every code back in time, past the one-minute wait or the day."""
    VerificationCode.objects.update(created_at=F("created_at") - timedelta(seconds=seconds))


def error(excinfo) -> tuple[str, int, dict]:
    return excinfo.value.error_code, excinfo.value.status_code, excinfo.value.extra


# --- The emails --------------------------------------------------------------------------------------

def test_the_code_email_is_in_telugu_by_default(mailoutbox):
    sent = send(make_pending(), language="te")
    message = mailoutbox[0]
    code = code_in(message)
    assert message.to == ["lakshmi@gmail.com"]
    assert message.subject == f"గ్రామ కనెక్ట్ కోడ్: {code}"
    assert "నమస్తే లక్ష్మి గారు," in message.body
    assert "మీ ఖాతా సృష్టించడానికి ఈ కోడ్ ఇవ్వండి:" in message.body
    html, mimetype = message.alternatives[0]
    assert mimetype == "text/html" and code in html and 'lang="te"' in html
    assert sent.as_dict(with_hint=True) == {"expires_in": 600, "resend_after": 60, "email_hint": "l***@gmail.com"}
    assert sent.as_dict() == {"expires_in": 600, "resend_after": 60}


@pytest.mark.parametrize(
    "purpose, first_line",
    [
        (P.SIGNUP, "Enter this code to create your account:"),
        (P.RESET_PASSWORD, "Enter this code to choose a new password:"),
        (P.CHANGE_EMAIL, "Enter this code to add this email to your account:"),
        (P.DELETE, "Enter this code to delete your account. Deleting it loses all your points."),
    ],
)
def test_each_purpose_has_its_own_first_line_in_english(mailoutbox, purpose, first_line):
    owner = make_pending() if purpose == P.SIGNUP else make_account()
    send(owner, purpose=purpose)
    message = mailoutbox[0]
    assert message.subject == f"Grama Connect code: {code_in(message)}"
    assert first_line in message.body
    assert "The code works for 10 minutes." in message.body


def test_an_unknown_language_falls_back_to_telugu(mailoutbox):
    send(make_pending(), language="fr")
    assert mailoutbox[0].subject.startswith("గ్రామ కనెక్ట్ కోడ్:")


@pytest.mark.parametrize(
    "kind, language, subject",
    [
        ("password_changed", "te", "మీ గ్రామ కనెక్ట్ పాస్‌వర్డ్ మారింది"),
        ("password_changed", "en", "Your Grama Connect password was changed"),
        ("email_changed", "en", "Your Grama Connect email was changed"),
        ("account_deleted", "te", "మీ గ్రామ కనెక్ట్ ఖాతా తొలగించబడింది"),
        ("account_deleted", "en", "Your Grama Connect account was deleted"),
    ],
)
def test_notices_go_in_the_citizens_language(mailoutbox, kind, language, subject):
    assert emails.notify(kind, "ravi@gmail.com", language=language, name="రవి", new_email="ravi.k@yahoo.com")
    assert mailoutbox[0].subject == subject
    assert "ravi.k@yahoo.com" not in mailoutbox[0].body  # the new address only masked


def test_the_email_changed_notice_names_the_new_address_masked(mailoutbox):
    emails.notify("email_changed", "ravi@gmail.com", language="en", new_email="ravi.k@yahoo.com")
    assert "changed to r***@yahoo.com." in mailoutbox[0].body


def test_a_notice_that_cannot_be_sent_does_not_fail(monkeypatch, mailoutbox):
    def down(self, fail_silently=False):
        raise smtplib.SMTPServerDisconnected("down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", down)
    assert emails.notify("password_changed", "ravi@gmail.com", language="te") is False


# --- The code rules ----------------------------------------------------------------------------------

def test_the_code_is_kept_only_as_a_keyed_hash(mailoutbox):
    send(make_pending())
    code = code_in(mailoutbox[0])
    row = VerificationCode.objects.get()
    stored = [str(value) for value in VerificationCode.objects.values().get().values()]
    assert code not in stored
    assert len(row.code_hash) == 64 and row.expires_at - row.created_at == timedelta(minutes=10)
    assert row.provider_message_id.startswith("<")


def test_the_right_code_works_once(mailoutbox):
    pending = make_pending()
    send(pending)
    row = codes.check_code(purpose=P.SIGNUP, code=code_in(mailoutbox[0]), pending_signup=pending)
    assert row.verified_at is not None
    assert codes.consume(row) is True
    assert codes.consume(row) is False
    with pytest.raises(ApiError) as excinfo:
        codes.check_code(purpose=P.SIGNUP, code=code_in(mailoutbox[0]), pending_signup=pending)
    assert error(excinfo)[:2] == ("code_expired", 400)


def test_a_wrong_code_counts_down_then_the_code_is_dead(mailoutbox):
    pending = make_pending()
    send(pending)
    code = code_in(mailoutbox[0])
    for left in (4, 3, 2, 1, 0):
        with pytest.raises(ApiError) as excinfo:
            codes.check_code(purpose=P.SIGNUP, code=other_than(code), pending_signup=pending)
        assert error(excinfo) == ("code_wrong", 400, {"attempts_left": left})
    with pytest.raises(ApiError) as excinfo:
        codes.check_code(purpose=P.SIGNUP, code=code, pending_signup=pending)
    assert error(excinfo)[0] == "code_expired"


def test_letters_or_a_short_code_count_as_a_wrong_try(mailoutbox):
    pending = make_pending()
    send(pending)
    with pytest.raises(ApiError) as excinfo:
        codes.check_code(purpose=P.SIGNUP, code="12a45", pending_signup=pending)
    assert error(excinfo) == ("code_wrong", 400, {"attempts_left": 4})


def test_a_code_expires_after_ten_minutes(mailoutbox):
    pending = make_pending()
    send(pending)
    VerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ApiError) as excinfo:
        codes.check_code(purpose=P.SIGNUP, code=code_in(mailoutbox[0]), pending_signup=pending)
    assert error(excinfo)[0] == "code_expired"


def test_only_the_newest_code_counts(mailoutbox):
    pending = make_pending()
    send(pending)
    age(61)
    send(pending)
    first, second = code_in(mailoutbox[0]), code_in(mailoutbox[1])
    if first != second:
        with pytest.raises(ApiError):
            codes.check_code(purpose=P.SIGNUP, code=first, pending_signup=pending)
    assert codes.check_code(purpose=P.SIGNUP, code=second, pending_signup=pending)


def test_a_code_of_another_purpose_does_not_work(mailoutbox):
    account = make_account()
    send(account, purpose=P.RESET_PASSWORD)
    with pytest.raises(ApiError) as excinfo:
        codes.check_code(purpose=P.DELETE, code=code_in(mailoutbox[0]), account=account)
    assert error(excinfo)[0] == "code_expired"


def test_a_new_code_waits_sixty_seconds(mailoutbox):
    pending = make_pending()
    send(pending)
    with pytest.raises(ApiError) as excinfo:
        send(pending)
    code, status, extra = error(excinfo)
    assert (code, status) == ("code_cooldown", 429) and 1 <= extra["retry_after_s"] <= 60
    age(61)
    send(pending)
    assert len(mailoutbox) == 2


def test_five_codes_a_day_per_address(mailoutbox):
    for _ in range(5):
        send(make_pending())
        age(61)
    with pytest.raises(ApiError) as excinfo:
        send(make_pending())
    assert error(excinfo)[:2] == ("code_limit", 429)
    age(24 * 3600)  # a day later the old codes no longer count
    send(make_pending())


def test_five_codes_a_day_per_account_whatever_the_address(mailoutbox):
    account = make_account()
    for n in range(5):
        send(account, purpose=P.CHANGE_EMAIL, email=f"new{n}@gmail.com")
        age(61)
    with pytest.raises(ApiError) as excinfo:
        send(account, purpose=P.CHANGE_EMAIL, email="new9@gmail.com")
    assert error(excinfo)[0] == "code_limit"


def test_ten_codes_a_day_per_phone(mailoutbox):
    for n in range(10):
        send(make_pending(f"person{n}@gmail.com"), phone=PHONE)
        age(61)
    with pytest.raises(ApiError) as excinfo:
        send(make_pending("person10@gmail.com"), phone=PHONE)
    assert error(excinfo)[0] == "code_limit"
    send(make_pending("person11@gmail.com"), phone="q" * 64)  # another phone is not limited


def test_the_email_service_down_answers_503_and_keeps_no_code(monkeypatch, mailoutbox):
    def down(self, fail_silently=False):
        raise smtplib.SMTPServerDisconnected("down")

    pending = make_pending()
    with monkeypatch.context() as patch:
        patch.setattr(EmailMultiAlternatives, "send", down)
        with pytest.raises(ApiError) as excinfo:
            send(pending)
    assert error(excinfo)[:2] == ("email_unavailable", 503)
    assert not VerificationCode.objects.exists()
    send(pending)  # the failed try costs nothing: no wait, no count
    assert len(mailoutbox) == 1


def test_an_audit_entry_or_a_log_line_never_holds_the_code_or_the_address(monkeypatch, mailoutbox):
    lines: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = Keep(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        send(make_pending("lakshmi@gmail.com"))

        def down(self, fail_silently=False):
            raise smtplib.SMTPRecipientsRefused({"suresh@gmail.com": (550, b"no")})

        with monkeypatch.context() as patch:
            patch.setattr(EmailMultiAlternatives, "send", down)
            with pytest.raises(ApiError):
                send(make_pending("suresh@gmail.com"))
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    code = code_in(mailoutbox[0])
    entry = AuditLog.objects.get(action="citizen.code_sent")
    assert entry.data == {"purpose": "SIGNUP", "channel": "EMAIL", "email": "l***@gmail.com"}
    stored = json.dumps(list(AuditLog.objects.values()), default=str, ensure_ascii=False) + "\n".join(lines)
    for secret in (code, "lakshmi@gmail.com", "suresh@gmail.com"):
        assert secret not in stored, secret
    assert any("not sent: SMTPRecipientsRefused" in line for line in lines)


# --- The server check --------------------------------------------------------------------------------

def test_servers_warn_while_email_is_not_set_up(settings):
    settings.DEBUG = False
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "localhost"
    found = email_not_set_up_on_server(None)
    assert [message.id for message in found] == ["citizens.W002"] and found[0].level == checks.WARNING
    settings.EMAIL_HOST = "email-smtp.ap-south-1.amazonaws.com"
    assert email_not_set_up_on_server(None) == []
    settings.DEBUG = True
    settings.EMAIL_HOST = "localhost"
    assert email_not_set_up_on_server(None) == []

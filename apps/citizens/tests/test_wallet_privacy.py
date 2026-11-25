"""The citizen wallet writes no full email, mobile number, password, code or token to the log, the audit
log or a background job's arguments (BVTGC-130; CLAUDE.md "Never log or audit passwords, tokens, or full
emails and mobile numbers"; contract section 21, "Privacy").

One test walks every wallet path that handles them, through the API: two sign-ups with the email code
(the second with the first one's referral code), logins by number and email with a wrong password first,
a forgotten password, an email change with its code, a mobile change, the Super Admin's find, new email,
number release, block, unblock, correction, rejection and exports, the website's deletion with its code
and the app's deletion with the password. Every log record of every logger, at every level, is caught
while it runs (the fixture of grama/tests/test_log_leak.py).
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.utils import timezone
from django_q.models import OrmQ, Schedule, Task

from apps.audit.models import AuditLog
from apps.citizens.models import VerificationCode
from apps.notifications.models import Device
from apps.wallet.models import Referral
from grama.tests.test_log_leak import log_records, rendered  # noqa: F401  (log_records is a fixture)

pytestmark = pytest.mark.django_db

V1 = "/api/v1"
A = {"name": "లక్ష్మి దేవి", "mobile": "9876543210", "email": "Lakshmi.Devi@gmail.com", "password": "Kaza-wallet-4821"}
B = {"name": "Ravi Kumar", "mobile": "9123456780", "email": "ravi.kumar@example.org", "password": "31957284"}
A_NEW_PASSWORD, A_NEW_EMAIL, A_NEW_MOBILE = "Nava-wallet-7319", "lakshmi.kotha@example.org", "9988776655"
B_NEW_EMAIL, WRONG = "ravi.new@example.org", "wrong-guess-2026"


def code_in(message) -> str:
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def past_the_wait() -> None:
    """Move every code back two minutes, past the 60-second wait between codes."""
    VerificationCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))


def sign_up(client, person, world, mailoutbox, secrets, **extra) -> dict:
    body = {
        "name": person["name"], "mobile": person["mobile"], "email": person["email"], "password": person["password"],
        "village_id": world.ref("kolakaluru").id, "age_confirmed": True, "consent_version": "2026-11-25", **extra,
    }
    started = client.post(f"{V1}/citizen/auth/signup/", body, format="json")
    assert started.status_code == 200, started.content
    token = started.json()["signup_token"]
    code = code_in(mailoutbox[-1])
    secrets.update({f"signup token {person['name']}": token, f"signup code {person['name']}": code})
    done = client.post(f"{V1}/citizen/auth/signup/verify/", {"signup_token": token, "code": code}, format="json")
    assert done.status_code == 201, done.content
    secrets[f"access {person['name']}"], secrets[f"refresh {person['name']}"] = done.json()["access"], done.json()["refresh"]
    return done.json()


def bearer(api, access: str):
    client = api()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


def stored_texts() -> list[str]:
    """Everything the audit log and the job queue hold, as text."""
    texts = []
    for entry in AuditLog.objects.all():
        texts.append(" ".join([entry.actor_name, entry.target_label, entry.target_id, json.dumps(entry.data, ensure_ascii=False)]))
    for task in Task.objects.all():
        texts.append(f"{task.func} {task.args!r} {task.kwargs!r} {task.result!r}")
    for queued in OrmQ.objects.all():
        texts.append(repr(queued.task()))
    for schedule in Schedule.objects.all():
        texts.append(f"{schedule.func} {schedule.args} {schedule.kwargs}")
    return texts


def leaks(secrets: dict, texts: list[str]) -> list[str]:
    found = []
    for label, value in secrets.items():
        value = str(value)
        if value.isdecimal():  # a code or a number: not inside a longer run of digits or letters
            pattern = re.compile(rf"(?<![0-9A-Za-z]){re.escape(value)}(?![0-9A-Za-z])")
        else:
            pattern = re.compile(re.escape(value), re.IGNORECASE)
        found += [f"{label} in: {text[:200]}" for text in texts if pattern.search(text)]
    return found


def test_the_whole_wallet_leaves_no_secret_in_the_log_audit_or_jobs(
    api, world, settings, mailoutbox, log_records, django_capture_on_commit_callbacks,  # noqa: F811
):
    settings.WALLET_ENABLED = True
    settings.WALLET_PILOT_MANDALS = []
    secrets = {
        "A's mobile": A["mobile"], "A's email": A["email"], "A's password": A["password"],
        "B's mobile": B["mobile"], "B's email": B["email"], "B's password": B["password"],
        "A's new password": A_NEW_PASSWORD, "A's new email": A_NEW_EMAIL, "A's new mobile": A_NEW_MOBILE,
        "B's new email": B_NEW_EMAIL, "a wrong password": WRONG,
    }
    client = api()
    with django_capture_on_commit_callbacks(execute=True):
        # Two sign-ups; B with A's referral code, from another phone.
        a = sign_up(client, A, world, mailoutbox, secrets, android_id="phone-a")
        sign_up(client, B, world, mailoutbox, secrets, android_id="phone-b", referral_code=a["account"]["referral_code"])
        Device.objects.create(user_id=Referral.objects.get().referrer_id, fcm_token="tok-a", platform="android")

        # Logins: a wrong password, then by number and by email.
        login = f"{V1}/citizen/auth/login/"
        assert client.post(login, {"login": A["mobile"], "password": WRONG}, format="json").status_code == 400
        assert client.post(login, {"login": A["mobile"], "password": A["password"]}, format="json").status_code == 200
        assert client.post(login, {"login": A["email"], "password": A["password"]}, format="json").status_code == 200

        # A forgotten password, reset with the emailed code.
        client.post(f"{V1}/citizen/auth/password/forgot/", {"login": A["email"]}, format="json")
        secrets["reset code"] = code_in(mailoutbox[-1])
        reset = client.post(f"{V1}/citizen/auth/password/reset/",
                            {"login": A["mobile"], "code": secrets["reset code"], "new_password": A_NEW_PASSWORD},
                            format="json")
        assert reset.status_code == 200, reset.content
        secrets["access after reset"] = reset.json()["access"]
        mine = bearer(api, reset.json()["access"])

        # A new email with its code, then a new number.
        past_the_wait()
        assert mine.post(f"{V1}/citizen/account/email/", {"email": A_NEW_EMAIL, "password": A_NEW_PASSWORD},
                         format="json").status_code == 200
        secrets["email change code"] = code_in(mailoutbox[-1])
        assert mine.post(f"{V1}/citizen/account/email/verify/", {"code": secrets["email change code"]},
                         format="json").status_code == 200
        assert mine.post(f"{V1}/citizen/account/mobile/", {"mobile": A_NEW_MOBILE, "password": A_NEW_PASSWORD},
                         format="json").status_code == 200

        # The Super Admin: find, a new email after a call-back, the number released, block, unblock,
        # a correction, a rejection with a comment, both exports.
        admin = api(world.users["super"])
        base = f"{V1}/admin/citizen-accounts/"
        found = admin.post(base + "find/", {"mobile": B["mobile"]}, format="json").json()["results"]
        assert admin.post(base + "find/", {"email": B["email"]}, format="json").json()["results"] == found
        b_id = found[0]["id"]
        assert admin.post(f"{base}{b_id}/email/", {"email": B_NEW_EMAIL, "reason": "Lost the email", "called_back": True},
                          format="json").status_code == 200
        assert admin.post(f"{base}{b_id}/release-mobile/", {"reason": "Number taken", "called_back": True},
                          format="json").status_code == 200
        for action in ("block", "unblock"):
            assert admin.post(f"{base}{b_id}/{action}/", {"reason": "Checked"}, format="json").status_code == 200
        a_id = Referral.objects.get().referrer_id
        assert admin.post(f"{V1}/admin/wallets/{a_id}/adjust/", {"points": 10, "reason": "Help-line fix"},
                          format="json").status_code == 200
        referral = Referral.objects.get()
        assert admin.post(f"{V1}/admin/referrals/{referral.id}/reject/", {"comment": "Same family"},
                          format="json").status_code == 200
        for kind in ("referrals", "entries"):
            assert admin.get(f"{V1}/admin/wallet/export/?type={kind}&format=csv").status_code == 200

        # The website's deletion with its code (B), and the app's with the password (A).
        client.post(f"{V1}/citizen/auth/delete/code/", {"login": B_NEW_EMAIL}, format="json")
        secrets["deletion code"] = code_in(mailoutbox[-1])
        assert client.post(f"{V1}/citizen/auth/delete/", {"login": B_NEW_EMAIL, "code": secrets["deletion code"]},
                           format="json").status_code == 200
        assert mine.post(f"{V1}/citizen/account/delete/", {"password": A_NEW_PASSWORD}, format="json").status_code == 204

    assert len(mailoutbox) >= 9  # the codes and the notices really went out
    assert AuditLog.objects.filter(action__startswith="citizen.").count() >= 12
    log_texts = [rendered(record) for record in log_records]
    assert log_texts, "the log catcher caught nothing: the test would prove nothing"
    assert leaks(secrets, log_texts) == []
    assert leaks(secrets, stored_texts()) == []

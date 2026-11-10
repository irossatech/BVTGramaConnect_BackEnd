"""The safety net in audit.services.record(): personal data is masked and secrets are removed
even when a caller forgets (CLAUDE.md, "Always"; BE-SEC-001)."""

import pytest

from apps.audit.masking import REMOVED, scrub, scrub_label
from apps.audit.services import record

pytestmark = pytest.mark.django_db


def test_full_contact_details_are_masked_at_any_depth():
    entry = record(
        "user.updated",
        data={
            "email": "ravi.kumar@example.in",
            "mobile": "+91 98765 43210",
            "changed": {"phone": {"from": "9000010001", "to": "9876543210"}},
            "alt_mobile": 9123456789,
            "phones": ["9000010001", "08645-123456"],
            "name_te": "రవి కుమార్",
            "phone_kind": "MOBILE",
        },
    )
    entry.refresh_from_db()
    assert entry.data == {
        "email": "r***@example.in",
        "mobile": "********3210",
        "changed": {"phone": {"from": "******0001", "to": "******3210"}},
        "alt_mobile": "******6789",
        "phones": ["******0001", "*******3456"],
        "name_te": "రవి కుమార్",
        "phone_kind": "MOBILE",
    }


def test_secrets_are_removed_but_password_notes_stay():
    entry = record(
        "user.created",
        data={
            "password": "Grama@2026-secret",
            "new_password": "Another-1",
            "fcm_token": "abc.def.ghi",
            "client_secret": "s3cr3t",
            "otp": 123456,
            "role": "VILLAGE_ADMIN",
        },
    )
    entry.refresh_from_db()
    assert entry.data == {
        "password": REMOVED, "new_password": REMOVED, "fcm_token": REMOVED,
        "client_secret": REMOVED, "otp": REMOVED, "role": "VILLAGE_ADMIN",
    }
    for note in ("generated", "set by admin", "none"):
        assert record("user.password_reset", data={"password": note}).data == {"password": note}


def test_already_masked_values_lose_nothing():
    data = {"phone": "******0001", "email": "r***@example.in", "changed": {"mobile": {"from": "", "to": "******3210"}}}
    assert scrub(data) == data
    assert scrub(scrub(data)) == data


def test_words_that_only_look_like_secrets_are_kept():
    data = {"footprint": "x", "key": "electricity", "tokens_left": 3, "username": "va_kaza", "comment": "otp camp"}
    assert scrub(data) == data


@pytest.mark.parametrize(
    "label, shown",
    [
        ("ravi.kumar@example.in", "r***@example.in"),
        ("9876543210", "******3210"),
        ("+91 98765 43210", "********3210"),
        ("Ravi Kumar", "Ravi Kumar"),
        ("రవి", "రవి"),
        ("Road closed 12-11-2026", "Road closed 12-11-2026"),
        ("", ""),
        (None, ""),
    ],
)
def test_target_label_that_is_only_an_email_or_phone_is_masked(label, shown):
    assert scrub_label(label) == shown
    assert record("user.updated", target_label=label).target_label == shown

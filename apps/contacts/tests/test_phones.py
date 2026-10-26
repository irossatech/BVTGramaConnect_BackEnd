"""phones.py: the phone grammar, table-driven (contacts plan, section 8)."""

import pytest

from apps.contacts.phones import InvalidPhone, mask, normalise_phone


@pytest.mark.parametrize(
    "raw, digits, kind",
    [
        ("9000010001", "9000010001", "MOBILE"),
        ("90000 10001", "9000010001", "MOBILE"),
        ("+91 90000 10001", "9000010001", "MOBILE"),
        ("+91-90000-10001", "9000010001", "MOBILE"),
        ("919000010001", "9000010001", "MOBILE"),
        ("6000010001", "6000010001", "MOBILE"),
        ("0863-2234567", "08632234567", "LANDLINE"),
        ("(08644) 222333", "08644222333", "LANDLINE"),
        ("0863223456", "0863223456", "LANDLINE"),
        ("1800 425 1110", "18004251110", "TOLL_FREE"),
        ("1800123456", "1800123456", "TOLL_FREE"),
    ],
)
def test_area_numbers(raw, digits, kind):
    assert normalise_phone(raw) == (digits, kind)


@pytest.mark.parametrize("raw, digits", [("108", "108"), ("112", "112"), ("1098", "1098"), ("155261", "155261")])
def test_short_codes_only_for_helplines(raw, digits):
    assert normalise_phone(raw, allow_short=True) == (digits, "SHORT_CODE")
    with pytest.raises(InvalidPhone, match="helplines only"):
        normalise_phone(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "5000010001",  # 10 digits, not a mobile prefix
        "12",  # too short
        "*#06#",  # USSD code: never into a tel: link
        "",
        "   ",
        "+44 20 7946 0958",  # not Indian
        "+919000",  # +91 with too few digits
        "90000100011",  # 11 digits, no leading 0
        "9000O10001",  # letter O
        "1234567",  # 7 digits: no kind
    ],
)
def test_rejects(raw):
    with pytest.raises(InvalidPhone):
        normalise_phone(raw, allow_short=True)


def test_mask_keeps_only_the_last_four():
    assert mask("9000010001") == "******0001"
    assert mask("08632234567") == "*******4567"

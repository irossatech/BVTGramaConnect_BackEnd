"""action_label: each audit entry in words, in the request's language (CROSS-CONV-002, BVTGC-100)."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.utils import translation

from apps.audit.labels import ACTION_LABELS, action_label
from apps.audit.services import record

pytestmark = pytest.mark.django_db

REPO = Path(settings.BASE_DIR)
#: The first part of every audit action code the server writes.
PREFIXES = (
    "post", "user", "auth", "area", "setting", "service", "service_request", "bill", "ad", "contact",
    "contacts", "contact_type", "citizen", "category", "template", "report", "push", "email", "group",
)
ACTION_SHAPED = re.compile(r"""["'](""" + "|".join(PREFIXES) + r""")\.([a-z_]+)["']""")


def server_action_codes() -> set[str]:
    """Every quoted string in the server code that looks like an audit action code, minus Django
    permission names (contacts.manage_contact) and field paths (category.key)."""
    found = set()
    for root in ("apps", "grama"):
        for path in (REPO / root).rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            found |= {f"{a}.{b}" for a, b in ACTION_SHAPED.findall(path.read_text(encoding="utf-8"))}
    permissions = {f"{app}.{code}" for app, code in Permission.objects.values_list("content_type__app_label", "codename")}
    return found - permissions - {"category.key"}


def documented_action_codes() -> set[str]:
    """The audit actions listed in docs/authorization.md (rows of its audit tables)."""
    codes = set()
    for line in (REPO / "docs" / "authorization.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| `"):
            codes |= set(re.findall(r"`([a-z_]+\.[a-z_]+)`", line.split("|")[1]))
    permissions = {f"{app}.{code}" for app, code in Permission.objects.values_list("content_type__app_label", "codename")}
    return {code for code in codes if code not in permissions and code.split(".")[0] in PREFIXES}


def test_every_action_the_server_writes_has_words():
    codes = server_action_codes()
    assert len(codes) > 60  # the scan really found them
    assert sorted(codes - set(ACTION_LABELS)) == []


def test_every_documented_action_has_words():
    assert sorted(documented_action_codes() - set(ACTION_LABELS)) == []


def test_every_label_has_its_own_telugu():
    for code, label in ACTION_LABELS.items():
        with translation.override("en"):
            english = str(label)
        with translation.override("te"):
            telugu = str(label)
        assert telugu != english, code
        assert re.search(r"[ఀ-౿]", telugu), code


def test_an_unknown_code_still_reads_as_words():
    with translation.override("en"):
        assert action_label("something.new") == "other action (something.new)"
    with translation.override("te"):
        assert action_label("something.new") == "ఇతర చర్య (something.new)"


def test_the_list_sends_action_label_in_the_requested_language(api, world, english):
    record("post.published", actor=world.users["ma_tenali"], target_label="గ్రామ సభ")
    record("citizen.registered", via="app")
    client = api(world.users["super"])
    rows = client.get("/api/v1/audit/", HTTP_ACCEPT_LANGUAGE="en").json()["results"]
    assert {row["action"]: row["action_label"] for row in rows} == {
        "post.published": "published a post",
        "citizen.registered": "registered a citizen phone",
    }
    rows = client.get("/api/v1/audit/", HTTP_ACCEPT_LANGUAGE="te").json()["results"]
    assert {row["action"]: row["action_label"] for row in rows} == {
        "post.published": "పోస్ట్‌ను ప్రచురించారు",
        "citizen.registered": "పౌరుని ఫోన్‌ను నమోదు చేశారు",
    }


def test_telugu_is_the_default(api, world):
    record("user.blocked", actor=world.users["da_guntur"])
    row = api(world.users["super"]).get("/api/v1/audit/").json()["results"][0]
    assert row["action"] == "user.blocked" and row["action_label"] == "వినియోగదారుని నిరోధించారు"


def test_the_code_stays_for_filters_and_older_clients(api, world):
    record("user.blocked", actor=world.users["da_guntur"])
    client = api(world.users["super"])
    row = client.get("/api/v1/audit/?action=user.blocked").json()["results"][0]
    assert row["action"] == "user.blocked"


def test_the_csv_export_has_the_words_last(api, world):
    record("post.published", actor=world.users["ma_tenali"], target_label="గ్రామ సభ")
    response = api(world.users["super"]).get("/api/v1/audit/export/?format=csv", HTTP_ACCEPT_LANGUAGE="en")
    header, row = response.content.decode("utf-8").splitlines()[:2]
    assert header.endswith(",via,what_happened")
    assert row.endswith(",published a post")

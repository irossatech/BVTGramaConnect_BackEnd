"""Audit entries carry their action in words (BVTGC-100).

GET /audit/ and the dashboard's recent_activity send `action_label` with every entry, in the request's
language (Telugu by default), so the console and the app never show a raw code such as citizen.registered.
Every action the server can record must have a label: the scan below fails for a new one without it.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.audit.labels import ACTION_LABELS, action_label
from apps.audit.services import record

pytestmark = pytest.mark.django_db
AUDIT = "/api/v1/audit/"

PREFIXES = sorted({key.split(".")[0] for key in ACTION_LABELS})
LITERAL = re.compile(r"[\"'](?:" + "|".join(PREFIXES) + r")\.[a-z_]+[\"']")
#: Strings that look like an action but are not one.
NOT_ACTIONS = {
    "category.key",  # a serializer field path
    "contacts.manage_contact", "contacts.manage_contact_types",  # permission names
}


def recorded_in_code() -> dict[str, str]:
    """Every "<area>.<action>" string in the backend's own code (tests and migrations left out)."""
    root = Path(settings.BASE_DIR)
    found = {}
    for path in [*(root / "apps").rglob("*.py"), *(root / "grama").rglob("*.py")]:
        if "tests" in path.parts or "migrations" in path.parts or path.name == "labels.py":
            continue
        for match in LITERAL.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(0)[1:-1], path.relative_to(root).as_posix())
    return {key: where for key, where in found.items() if key not in NOT_ACTIONS}


def test_every_action_the_server_records_has_words():
    actions = recorded_in_code()
    assert len(actions) > 60  # the scan really reads the code
    missing = {key: where for key, where in actions.items() if key not in ACTION_LABELS}
    assert not missing, f"Give these audit actions a label in apps/audit/labels.py: {missing}"


def test_the_post_review_actions_are_covered():
    from apps.posts.services import AUDIT_ACTIONS

    assert set(AUDIT_ACTIONS.values()) <= set(ACTION_LABELS)


def test_labels_are_telugu_by_default_and_english_on_request(english):
    assert action_label("post.published") == "published a post"
    assert action_label("citizen.registered") == "registered a citizen phone"


def test_telugu_by_default():
    assert action_label("post.published") == "పోస్ట్‌ను ప్రచురించారు"
    assert action_label("citizen.registered") == "పౌరుడి ఫోన్‌ను నమోదు చేశారు"


def test_an_unknown_action_is_never_shown_as_its_code(english):
    assert action_label("something.new") == "other activity"


def test_the_audit_list_sends_the_label(api, world):
    record("citizen.registered", actor=None, via="citizen")
    record("post.published", actor=world.users["va_kolakaluru"], via="app")
    rows = api(world.users["super"]).get(AUDIT).json()["results"]
    labels = {row["action"]: row["action_label"] for row in rows}
    assert labels["citizen.registered"] == "పౌరుడి ఫోన్‌ను నమోదు చేశారు"
    assert labels["post.published"] == "పోస్ట్‌ను ప్రచురించారు"


def test_the_audit_list_in_english(api, world):
    record("user.password_reset", actor=world.users["super"], via="console")
    rows = api(world.users["super"]).get(AUDIT, HTTP_ACCEPT_LANGUAGE="en").json()["results"]
    assert rows[0]["action"] == "user.password_reset" and rows[0]["action_label"] == "reset a user's password"


def test_the_dashboards_recent_activity_sends_the_label(api, world):
    record("area.created", actor=world.users["super"], via="console")
    data = api(world.users["super"]).get("/api/v1/admin/dashboard/", HTTP_ACCEPT_LANGUAGE="en").json()
    entry = next(e for e in data["recent_activity"] if e["action"] == "area.created")
    assert entry["action_label"] == "added an area"

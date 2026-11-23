"""Server messages follow Accept-Language: Telugu by default, English on request (BVTGC-29, CROSS-LANG-001).

The Telugu lives in locale/te/LC_MESSAGES/django.po, kept complete by grama/i18n.py.
"""

import ast
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import translation

from grama import i18n

WRONG_LOGIN_TE = "యూజర్ పేరు లేదా పాస్‌వర్డ్ తప్పు."
WRONG_LOGIN_EN = "Wrong username or password."
REQUIRED_TE = "ఈ ఫీల్డ్ తప్పనిసరి."


# --- The catalogue itself ------------------------------------------------------------------------


def test_every_message_has_telugu_and_the_mo_is_up_to_date():
    """Fails when someone adds a message without its Telugu: run `python manage.py translations`."""
    assert i18n.check() == []


def test_the_compiled_catalogue_is_committed():
    assert i18n.mo_path().is_file() and i18n.po_path().is_file()
    assert Path(settings.LOCALE_PATHS[0]) == i18n.po_path().parents[2]


def test_the_check_catches_a_lost_placeholder(tmp_path, monkeypatch):
    import polib

    po = polib.POFile(wrapwidth=0)
    po.metadata = dict(i18n.HEADER)
    po.append(polib.POEntry(msgid="At most %(n)d photos.", msgstr="గరిష్టం ఫోటోలు."))  # %(n)d dropped
    po.append(polib.POEntry(msgid="Ensure this field has at least {min_length} characters.", msgstr="కనీసం {min} అక్షరాలు"))
    path = tmp_path / "django.po"
    po.save(str(path))
    po.save_as_mofile(str(tmp_path / "django.mo"))
    monkeypatch.setattr(i18n, "po_path", lambda: path)
    monkeypatch.setattr(i18n, "mo_path", lambda: tmp_path / "django.mo")
    extracted = i18n.Extracted({"At most %(n)d photos.": ["x.py:1"], "Ensure this field has at least {min_length} characters.": ["x.py:2"]})
    problems = i18n.check(extracted)
    assert len(problems) == 2 and all(problem.startswith("placeholders differ") for problem in problems)


def test_the_check_catches_a_missing_translation_and_a_stale_mo(tmp_path, monkeypatch):
    import polib

    po = polib.POFile(wrapwidth=0)
    po.metadata = dict(i18n.HEADER)
    po.append(polib.POEntry(msgid="Too many photos.", msgstr=""))
    path = tmp_path / "django.po"
    po.save(str(path))
    monkeypatch.setattr(i18n, "po_path", lambda: path)
    monkeypatch.setattr(i18n, "mo_path", lambda: tmp_path / "django.mo")
    problems = i18n.check(i18n.Extracted({"Too many photos.": ["x.py:1"], "New one.": ["x.py:2"]}))
    assert any(problem.startswith("no Telugu: 'Too many photos.'") for problem in problems)
    assert any(problem.startswith("not in the .po") and "New one." in problem for problem in problems)
    assert any("django.mo is missing" in problem for problem in problems)


def test_a_message_that_is_not_a_literal_is_reported(tmp_path, monkeypatch):
    (tmp_path / "apps").mkdir()
    (tmp_path / "grama").mkdir()
    (tmp_path / "apps" / "x.py").write_text(
        "from django.utils.translation import gettext as _\nfrom django.utils.translation import ngettext\n"
        "def f(word):\n    return _(word), _('Fine.'), ngettext('a', 'b', 2)\n",
        encoding="utf-8",
    )
    extracted = i18n.extract(tmp_path)
    assert "Fine." in extracted.messages
    assert [problem.split(": ", 1)[1] for problem in extracted.problems] == [
        "gettext() needs a literal string to be translated",
        "ngettext() is not supported by grama/i18n.py yet",
    ]


# --- What clients see ----------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("language", "expected"),
    [("te", WRONG_LOGIN_TE), ("en", WRONG_LOGIN_EN), (None, WRONG_LOGIN_TE), ("te-IN,te;q=0.9", WRONG_LOGIN_TE)],
)
def test_login_errors_come_in_the_requested_language(api, language, expected):
    headers = {"HTTP_ACCEPT_LANGUAGE": language} if language else {}
    response = api().post("/api/v1/auth/login/", {"username": "nobody", "password": "wrong"}, format="json", **headers)
    assert response.status_code == 400
    assert response.json()["error"] == {"code": "invalid_credentials", "message": expected}
    assert response["Content-Language"] == ("en" if language == "en" else "te")
    assert "Accept-Language" in response["Vary"]  # caches keep the two languages apart


@pytest.mark.django_db
def test_drf_field_errors_are_telugu_too(api):
    response = api().post("/api/v1/auth/login/", {}, format="json", HTTP_ACCEPT_LANGUAGE="te")
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == REQUIRED_TE
    assert error["fields"] == {"username": [REQUIRED_TE], "password": [REQUIRED_TE]}


@pytest.mark.django_db
def test_messages_with_numbers_keep_them(api):
    response = api().get("/api/v1/feed/", {"village_id": "1", "q": "a"}, HTTP_ACCEPT_LANGUAGE="te")
    # The village may not exist; either way the message is Telugu and keeps its number.
    message = response.json()["error"]["message"]
    assert message in {"ఈ గ్రామం లేదు.", f"కనీసం {settings.FEED_SEARCH_MIN_CHARS} అక్షరాలతో వెతకండి."}


@pytest.mark.django_db
def test_telugu_goes_out_as_raw_utf8(api):
    response = api().post("/api/v1/auth/login/", {"username": "nobody", "password": "wrong"}, format="json")
    assert WRONG_LOGIN_TE.encode() in response.content
    assert b"\\u0c" not in response.content.lower()


# --- Text built from parts -----------------------------------------------------------------------


def test_service_request_status_names_follow_the_push_language():
    from apps.service_requests.models import RequestStatus, status_label

    assert status_label(RequestStatus.APPROVED, "te") == "ఆమోదించబడింది"
    assert status_label(RequestStatus.APPROVED, "en") == "Approved"
    assert status_label(RequestStatus.IN_PROGRESS, "") == "ప్రక్రియలో ఉంది"  # Telugu is the default
    assert translation.get_language() == settings.LANGUAGE_CODE  # override() put the language back


def test_system_history_notes_are_saved_in_english_and_shown_in_the_readers_language():
    from apps.service_requests import services

    for saved, message in services.SYSTEM_NOTES.items():
        with translation.override("en"):
            assert str(message) == saved  # the literal in gettext_lazy is the saved text
    with translation.override("te"):
        assert services.system_note(services.CANCELLED_NOTE) == "పౌరుడు రద్దు చేశారు"
        assert services.system_note("The road is fixed.") == "The road is fixed."  # an official's own words
    with translation.override("en"):
        assert services.system_note(services.AUTO_ASSIGNED_NOTE) == services.AUTO_ASSIGNED_NOTE


def _literal_actions():
    """Every literal action passed to invalid_transition / _own_editable in the posts app."""
    actions = set()
    for path in (Path(settings.BASE_DIR) / "apps" / "posts").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"invalid_transition", "_own_editable"}:
                last = node.args[-1]
                if isinstance(last, ast.Constant):
                    actions.add(last.value)
    return actions


def test_every_refused_post_action_has_its_own_sentence():
    from apps.posts.services import TRANSITION_MESSAGES, VERBS

    assert _literal_actions() | set(VERBS.values()) <= set(TRANSITION_MESSAGES)


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "A taken down post cannot be edited."),
    ("te", "ఈ పోస్ట్ 'తొలగించారు' స్థితిలో ఉంది; దీన్ని సవరించలేరు."),
])
def test_a_refused_post_action_reads_as_one_sentence(language, expected):
    from apps.posts.models import Post, PostStatus
    from apps.posts.services import invalid_transition

    with translation.override(language):
        error = invalid_transition(Post(status=PostStatus.UNPUBLISHED), "edited")
    assert str(error.detail) == expected
    assert error.extra == {"current_status": "UNPUBLISHED"}

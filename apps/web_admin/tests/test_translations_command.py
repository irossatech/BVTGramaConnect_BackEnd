"""manage.py translations: the command around grama/i18n.py (stage 6 finding BE-TEST-207, BVTGC-112).

grama/tests/test_i18n.py tests the catalogue code itself. This tests what the command does with it:
--check only checks; a normal run updates the catalogue, builds django.mo and lists the messages that
still need Telugu; a message that is not a plain literal stops it before anything is written. Only
the --check run reads the real catalogue; the others use fakes, so the committed files never change.
"""

import pytest
from django.core.management import CommandError, call_command

from grama import i18n


@pytest.fixture
def fake_catalogue(monkeypatch):
    """Record what the command asks of grama.i18n, without touching locale/."""
    calls = []
    extracted = i18n.Extracted({"Too many photos.": ["x.py:1"], "New one.": ["x.py:2"]})
    monkeypatch.setattr(i18n, "extract", lambda: extracted)
    monkeypatch.setattr(i18n, "update", lambda found: calls.append("update") or (["New one."], ["Old one."]))
    monkeypatch.setattr(i18n, "compile_mo", lambda: calls.append("compile_mo"))
    monkeypatch.setattr(i18n, "check", lambda found=None: calls.append("check") or [])
    return calls, extracted


def test_check_on_the_committed_catalogue_passes(capsys):
    call_command("translations", "--check")
    assert "Telugu catalogue complete" in capsys.readouterr().out


def test_check_fails_on_a_problem_and_writes_nothing(fake_catalogue, monkeypatch):
    calls, _extracted = fake_catalogue
    monkeypatch.setattr(i18n, "check", lambda found=None: ["no Telugu: 'New one.' (x.py:2)"])
    with pytest.raises(CommandError, match="1 problem"):
        call_command("translations", "--check")
    assert "update" not in calls and "compile_mo" not in calls


def test_a_normal_run_updates_builds_and_lists_what_needs_telugu(fake_catalogue, capsys):
    calls, _extracted = fake_catalogue
    call_command("translations")
    assert calls == ["update", "compile_mo", "check"]
    out = capsys.readouterr().out
    assert "2 messages; 1 added, 1 no longer used." in out
    assert "new, needs Telugu: New one." in out


def test_a_message_that_is_not_a_literal_stops_the_run_before_writing(fake_catalogue):
    calls, extracted = fake_catalogue
    extracted.problems.append("x.py:3: _() needs a plain string literal")
    with pytest.raises(CommandError, match="plain string literal"):
        call_command("translations")
    assert calls == []

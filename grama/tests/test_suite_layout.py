"""Every test file sits in exactly one category of grama/tests/suite.json (audit stage S6, BVTGC-17).

python -m grama.testsuite refuses to run otherwise; this test makes a plain pytest run, and so CI,
fail the same way, so a new test file cannot be left out of the category report.
"""

from grama import testsuite


def test_every_test_file_has_exactly_one_category():
    assert testsuite.problems(testsuite.load_config()) == []


def test_every_category_has_a_title_and_files():
    for key, category in testsuite.load_config()["categories"].items():
        assert category["title"] and category["files"], key


def test_a_new_file_without_a_category_is_reported(monkeypatch):
    real = testsuite.test_files()
    monkeypatch.setattr(testsuite, "test_files", lambda: real | {"apps/posts/tests/test_brand_new.py"})
    assert testsuite.problems(testsuite.load_config()) == ["in no category: apps/posts/tests/test_brand_new.py"]

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


def test_coverage_is_summed_per_app_and_the_least_covered_files_come_first():
    def file(statements, covered):
        return {"summary": {"num_statements": statements, "covered_lines": covered}}

    summary = testsuite.coverage_summary({"files": {
        "apps\\posts\\services.py": file(200, 180),   # Windows paths too
        "apps/posts/views.py": file(100, 90),
        "apps/feed/views.py": file(50, 10),
        "grama/http.py": file(10, 0),                  # under 20 statements: not in the least-covered list
    }})
    assert summary["parts"]["apps/posts"] == {"statements": 300, "covered": 270, "percent": 90.0}
    assert summary["parts"]["apps/feed"]["percent"] == 20.0
    assert summary["parts"]["grama"]["percent"] == 0.0
    assert summary["total"] == {"statements": 360, "covered": 280, "percent": 77.8}
    assert [f["file"] for f in summary["least_covered"]] == [
        "apps/feed/views.py", "apps/posts/services.py", "apps/posts/views.py",
    ]


def test_a_new_file_without_a_category_is_reported(monkeypatch):
    real = testsuite.test_files()
    monkeypatch.setattr(testsuite, "test_files", lambda: real | {"apps/posts/tests/test_brand_new.py"})
    assert testsuite.problems(testsuite.load_config()) == ["in no category: apps/posts/tests/test_brand_new.py"]

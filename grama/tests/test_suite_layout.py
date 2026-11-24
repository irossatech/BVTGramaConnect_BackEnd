"""Every test file sits in exactly one category of grama/tests/suite.json (audit stage S6, BVTGC-17).

python -m grama.testsuite refuses to run otherwise; this test makes a plain pytest run, and so CI,
fail the same way, so a new test file cannot be left out of the category report.
"""

from types import SimpleNamespace

from grama import testsuite, testsuite_plugin


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


def phase(nodeid, when, outcome="passed", longrepr=None, duration=0.5):
    """A stand-in for one pytest TestReport (setup, call or teardown)."""
    return SimpleNamespace(nodeid=nodeid, when=when, duration=duration, longrepr=longrepr, longreprtext="",
                           failed=outcome == "failed", skipped=outcome == "skipped")


def outcomes(*phases):
    results = {}
    for report in phases:
        testsuite_plugin.record(results, report)
    return {nodeid: (entry["outcome"], entry["message"]) for nodeid, entry in results.items()}


def test_the_plugin_keeps_each_tests_outcome_and_reason():
    ok, bad, broken, skipped = (f"apps/posts/tests/test_x.py::test_{n}" for n in ("ok", "bad", "broken", "skipped"))
    crash = SimpleNamespace(reprcrash=SimpleNamespace(message="assert 400 == 401"))
    got = outcomes(
        phase(ok, "setup"), phase(ok, "call"), phase(ok, "teardown"),
        phase(bad, "setup"), phase(bad, "call", "failed", crash), phase(bad, "teardown"),
        phase(broken, "setup", "failed", crash), phase(broken, "teardown"),
        phase(skipped, "setup", "skipped", ("x.py", 3, "Skipped: needs ffmpeg and ffprobe")), phase(skipped, "teardown"),
    )
    assert got == {
        ok: ("passed", ""),
        bad: ("failed", "assert 400 == 401"),
        broken: ("error", "assert 400 == 401"),  # a failing setup is an error, and the teardown does not hide it
        skipped: ("skipped", "needs ffmpeg and ffprobe"),
    }


def test_the_plugin_splits_file_and_name_and_adds_up_the_time():
    results = {}
    nodeid = "grama/tests/test_y.py::TestThing::test_z[te]"
    for when in ("setup", "call", "teardown"):
        testsuite_plugin.record(results, phase(nodeid, when, duration=0.25))
    assert results[nodeid] == {"file": "grama/tests/test_y.py", "name": "TestThing::test_z[te]", "seconds": 0.75,
                               "outcome": "passed", "message": ""}


def test_a_new_file_without_a_category_is_reported(monkeypatch):
    real = testsuite.test_files()
    monkeypatch.setattr(testsuite, "test_files", lambda: real | {"apps/posts/tests/test_brand_new.py"})
    assert testsuite.problems(testsuite.load_config()) == ["in no category: apps/posts/tests/test_brand_new.py"]

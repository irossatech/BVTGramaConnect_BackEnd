"""Every test file sits in exactly one category of grama/tests/suite.json (audit stage S6, BVTGC-17).

python -m grama.testsuite refuses to run otherwise; this test makes a plain pytest run, and so CI,
fail the same way, so a new test file cannot be left out of the category report.
"""

from types import SimpleNamespace

from grama import testdashboard, testsuite, testsuite_plugin


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


def test_the_report_shows_when_the_run_started_not_when_it_ended(monkeypatch, tmp_path):
    from datetime import datetime

    monkeypatch.setattr(testsuite, "REPORTS", tmp_path)
    started = datetime(2026, 11, 24, 11, 51, 0, tzinfo=testsuite.IST)
    result = {"file": "grama/tests/test_y.py", "name": "test_z", "seconds": 480.0, "outcome": "passed", "message": ""}
    report = testsuite.write_report("security", testsuite.load_config(), ["security"], [result], 0, 480.0,
                                    started=started)
    assert report.name == "BE_TESTS_security_2026-11-24_11-51-00.md"
    assert "| Started | 2026-11-24 11:51:00 IST |" in report.read_text(encoding="utf-8")


def test_an_xfail_is_recorded_as_a_known_bug_with_its_finding():
    nodeid = "grama/tests/test_attack_inputs.py::test_x"
    xfail = SimpleNamespace(nodeid=nodeid, when="call", duration=0.1, longrepr=None, longreprtext="",
                            failed=False, skipped=True, wasxfail="BE-TEST-208: an area id too large")
    results = {}
    testsuite_plugin.record(results, phase(nodeid, "setup"))
    testsuite_plugin.record(results, xfail)
    assert (results[nodeid]["outcome"], results[nodeid]["message"]) == ("xfailed", "BE-TEST-208: an area id too large")


def test_every_category_maps_to_stage_markers_and_every_file_gets_its_markers():
    config = testsuite.load_config()
    assert set(config["markers"]) == set(config["categories"])
    stage = {name for names in config["markers"].values() for name in names}
    assert stage <= {"unit", "integration", "rbac", "contract", "security", "db", "concurrency", "jobs", "errors"}
    by_file = testsuite_plugin.markers_by_file(config)
    assert by_file["grama/tests/test_rbac_matrix.py"][:2] == ["permissions", "rbac"]
    assert set(testsuite_plugin.all_markers(config)) >= {"unit", "rbac", "security", "permissions"}


def test_the_dashboard_weights_cover_every_category_and_the_parts_add_up_to_100():
    board = testsuite.load_config()["dashboard"]
    assert set(board["weights"]) == set(testsuite.load_config()["categories"])
    assert sum(board["parts"].values()) == 100


def figures(called=2, rbac=1, every=4):
    rows = [["GET", f"api/v1/r{index}/"] for index in range(every)] + [["GET", "p/{}/"]]  # not under /api/v1/
    return {"all": rows, "called": rows[:called] + [["GET", "p/{}/"]], "rbac": rows[:rbac]}


def test_api_and_rbac_coverage_count_only_the_api():
    api = testdashboard.api_figures(figures())
    assert (api["endpoints"], api["called"], api["rbac"]) == (4, 2, 1)
    assert (api["api_percent"], api["rbac_percent"]) == (50.0, 25.0)
    assert api["not_called"] == ["GET /api/v1/r2/", "GET /api/v1/r3/"]


def test_the_health_score_and_risk():
    config = testsuite.load_config()
    everything = testdashboard.api_figures(figures(called=4, rbac=4))
    perfect = {key: {"tests": 10, "passed": 10} for key in config["categories"]}
    score = testdashboard.health(config, perfect, {"total": {"percent": 96.0}}, everything)
    assert score["score"] == 100.0 and score["risk"] == "LOW"

    one_failure = {**perfect, "security": {"tests": 10, "passed": 9, "failed": 1}}
    score = testdashboard.health(config, one_failure, {"total": {"percent": 96.0}}, everything)
    assert score["risk"] == "MEDIUM"  # a failed test is never LOW, however high the score

    known_bug = {**perfect, "security": {"tests": 10, "passed": 9, "xfailed": 1}}
    assert testdashboard.health(config, known_bug, {"total": {"percent": 96.0}}, everything)["score"] < 100

    # Without --coverage the score is out of the other 90 points, scaled to 100: a perfect run is still 100.
    unmeasured = testdashboard.health(config, perfect, None, everything)
    assert unmeasured["score"] == 100.0 and not unmeasured["line_measured"] and "line_coverage" not in unmeasured["parts"]

    nothing = {key: {"tests": 10, "passed": 2, "failed": 8} for key in config["categories"]}
    assert testdashboard.health(config, nothing, None, testdashboard.api_figures(figures(0, 0)))["risk"] == "HIGH"


def test_a_run_writes_its_dashboard_and_an_html_copy(monkeypatch, tmp_path):
    from datetime import datetime

    monkeypatch.setattr(testsuite, "REPORTS", tmp_path)
    started = datetime(2026, 11, 26, 18, 0, 0, tzinfo=testsuite.IST)
    result = {"file": "grama/tests/test_y.py", "name": "test_z", "seconds": 1.0, "outcome": "passed", "message": ""}
    testsuite.write_report("security", testsuite.load_config(), ["security"], [result], 0, 1.0, started=started,
                           routes=figures())
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["BE_TESTS_security_2026-11-26_18-00-00.html", "BE_TESTS_security_2026-11-26_18-00-00.json",
                     "BE_TESTS_security_2026-11-26_18-00-00.md", "backend-test-dashboard-2026-11-26_18-00-00.html"]
    page = (tmp_path / "backend-test-dashboard-2026-11-26_18-00-00.html").read_text(encoding="utf-8")
    assert "Health score" in page and "API coverage" in page and "RBAC matrix" in page


def test_a_new_file_without_a_category_is_reported(monkeypatch):
    real = testsuite.test_files()
    monkeypatch.setattr(testsuite, "test_files", lambda: real | {"apps/posts/tests/test_brand_new.py"})
    assert testsuite.problems(testsuite.load_config()) == ["in no category: apps/posts/tests/test_brand_new.py"]

"""pytest plugin for grama.testsuite: every test's outcome, written as JSON (audit stage S6, BVTGC-17).

grama.testsuite loads it with `-p grama.testsuite_plugin` and names the output file in the
GRAMA_TESTSUITE_RESULTS environment variable. Without that variable it does nothing, so a normal
pytest run is unchanged. It replaces pytest's JUnit XML, which the runner would otherwise have to
parse (the security scans refuse the standard library's XML parser).
"""

from __future__ import annotations

import json
import os

ENV = "GRAMA_TESTSUITE_RESULTS"

_results: dict[str, dict] = {}


def _message(report) -> str:
    """A skip's reason, or the first line of a failure."""
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:  # a skip: (path, line, "Skipped: reason")
        reason = str(longrepr[2])
        return reason.removeprefix("Skipped: ")
    crash = getattr(longrepr, "reprcrash", None)
    if crash is not None and getattr(crash, "message", None):
        return str(crash.message)
    text = getattr(report, "longreprtext", "") or ""
    return text.strip().splitlines()[-1] if text.strip() else ""


def record(results: dict[str, dict], report) -> None:
    """Fold one phase (setup, call or teardown) of one test into its entry.

    The call decides passed, failed or skipped. A failing setup or teardown is an error, and a skip
    in setup (skipif, a skipping fixture) is a skip. A later passing phase never hides an earlier
    failure or skip.
    """
    file, _, name = report.nodeid.partition("::")
    entry = results.setdefault(report.nodeid, {
        "file": file.replace("\\", "/"), "name": name, "seconds": 0.0, "outcome": "passed", "message": "",
    })
    entry["seconds"] += report.duration or 0.0
    if report.failed:
        outcome = "failed" if report.when == "call" else "error"
    elif report.skipped:
        outcome = "skipped"
    else:
        return
    if entry["outcome"] in ("failed", "error"):
        return
    entry["outcome"], entry["message"] = outcome, _message(report)


def pytest_runtest_logreport(report):
    if os.environ.get(ENV):
        record(_results, report)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get(ENV)
    if path:
        with open(path, "w", encoding="utf-8") as out:
            json.dump(list(_results.values()), out, ensure_ascii=False)

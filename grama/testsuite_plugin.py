"""pytest plugin for the backend test suite (audit stage S6, BVTGC-17). pytest.ini loads it in every run.

Markers, in every run: each test gets the key of its category in grama/tests/suite.json ("permissions",
"auth", ...) and the stage's markers that category maps to ("rbac", "security", "integration", ...),
plus "unit" when it never touches the database. So `pytest -m rbac` or `pytest -m unit` works.

Results, only when grama.testsuite runs the tests: it names an output file in GRAMA_TESTSUITE_RESULTS,
and this plugin writes every test's outcome there as JSON, and next to it (".routes.json") every API
route the tests called and every route the server has, for the dashboard's API coverage. Without that
variable nothing is written. It replaces pytest's JUnit XML, which the runner would otherwise have to
parse (the security scans refuse the standard library's XML parser).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ENV = "GRAMA_TESTSUITE_RESULTS"
SUITE = Path(__file__).resolve().parent / "tests" / "suite.json"
PARAM = re.compile(r"\(\?P<[^>]+>[^)]*\)|<[^>]+>|\{[^}]+\}")
DB_FIXTURES = {"db", "transactional_db", "django_db_reset_sequences", "django_db_serialized_rollback"}

_results: dict[str, dict] = {}
_calls: set[tuple[str, str]] = set()


def _suite() -> dict:
    return json.loads(SUITE.read_text(encoding="utf-8"))


def markers_by_file(suite: dict) -> dict[str, list[str]]:
    """Each test file's markers: its category's key and the stage markers that category maps to."""
    stage = suite.get("markers", {})
    return {name: [key, *stage.get(key, [])] for key, category in suite["categories"].items()
            for name in category["files"]}


def all_markers(suite: dict) -> dict[str, str]:
    found = {key: f"category: {category['title']}" for key, category in suite["categories"].items()}
    for key, names in suite.get("markers", {}).items():
        for name in names:
            found.setdefault(name, f"stage marker (from the {key} category and others)")
    found["unit"] = "the test never touches the database"
    return found


def touches_database(item) -> bool:
    return item.get_closest_marker("django_db") is not None or bool(DB_FIXTURES & set(getattr(item, "fixturenames", ())))


def pytest_configure(config):
    for name, about in all_markers(_suite()).items():
        config.addinivalue_line("markers", f"{name}: {about}")
    if os.environ.get(ENV):
        _record_client_calls()


def pytest_collection_modifyitems(config, items):
    import pytest

    by_file = markers_by_file(_suite())
    for item in items:
        file = item.nodeid.partition("::")[0].replace("\\", "/")
        for name in by_file.get(file, []):
            item.add_marker(getattr(pytest.mark, name))
        if not touches_database(item):
            item.add_marker(pytest.mark.unit)


# --- Outcomes -------------------------------------------------------------------------------------------


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
    failure or skip. An xfail (a known bug, marked with its finding ID) counts as "xfailed".
    """
    file, _, name = report.nodeid.partition("::")
    entry = results.setdefault(report.nodeid, {
        "file": file.replace("\\", "/"), "name": name, "seconds": 0.0, "outcome": "passed", "message": "",
    })
    entry["seconds"] += report.duration or 0.0
    if hasattr(report, "wasxfail") and report.skipped:
        outcome = "xfailed"
    elif report.failed:
        outcome = "failed" if report.when == "call" else "error"
    elif report.skipped:
        outcome = "skipped"
    else:
        return
    if entry["outcome"] in ("failed", "error"):
        return
    entry["outcome"] = outcome
    entry["message"] = str(report.wasxfail) if outcome == "xfailed" else _message(report)


def pytest_runtest_logreport(report):
    if os.environ.get(ENV):
        record(_results, report)


# --- API routes called, for the dashboard's API coverage --------------------------------------------------


def _record_client_calls() -> None:
    """Note (METHOD, path) of every request a test client sends, without changing the request."""
    from django.test.client import Client

    original = Client.request

    def request(self, **environ):
        _calls.add((str(environ.get("REQUEST_METHOD", "GET")).upper(), str(environ.get("PATH_INFO", ""))))
        return original(self, **environ)

    Client.request = request


def normal(route: str) -> str:
    """A route as written in the contract tests: parameters as {}, without regex anchors (DRF routers
    put "^" and "$" in the middle of a joined route)."""
    route = PARAM.sub("{}", route)
    for token in ("\\Z", "^", "$"):
        route = route.replace(token, "")
    return route.replace("\\/", "/").replace("\\.", ".")


def route_methods() -> set[tuple[str, str]]:
    """(METHOD, route) for every method each route of the URLconf answers, parameters as {}."""
    from django.urls import get_resolver
    from django.urls.resolvers import URLResolver

    found = set()

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, route)
                continue
            callback = pattern.callback
            view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            if view is None or "(?P<format>" in route:
                continue
            actions = getattr(callback, "actions", None)
            names = actions.keys() if actions else [m for m in ("get", "post", "put", "patch", "delete") if hasattr(view, m)]
            for method in names:
                if method.lower() in ("head", "options"):  # DRF routers add HEAD to every GET; nobody calls it
                    continue
                found.add((method.upper(), normal(route)))

    walk(get_resolver().url_patterns)
    return found


def routes_called(calls: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """The (METHOD, route) each recorded call reached; calls to unknown paths are left out."""
    from django.urls import Resolver404, resolve

    reached = set()
    for method, path in calls:
        try:
            match = resolve(path)
        except Resolver404:
            continue
        reached.add((method, normal(match.route)))
    return reached


RBAC_MATRIX = Path(__file__).resolve().parent / "tests" / "config" / "rbac-matrix.json"


def rbac_routes() -> set[tuple[str, str]]:
    """(METHOD, route) of every endpoint grama/tests/config/rbac-matrix.json checks for every role."""
    from django.urls import Resolver404, resolve

    found = set()
    for row in json.loads(RBAC_MATRIX.read_text(encoding="utf-8"))["endpoints"]:
        try:
            match = resolve("/api/v1/" + row["path"].split("?")[0])
        except Resolver404:
            continue
        found.add((row["method"], normal(match.route)))
    return found


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get(ENV)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as out:
        json.dump(list(_results.values()), out, ensure_ascii=False)
    try:
        everything = route_methods()
        figures = {"all": sorted(everything), "called": sorted(routes_called(_calls) & everything),
                   "rbac": sorted(rbac_routes() & everything)}
    except Exception:  # Django not set up (a run with no Django test): no route figures
        figures = {"all": [], "called": [], "rbac": []}
    with open(path + ".routes.json", "w", encoding="utf-8") as out:
        json.dump(figures, out)

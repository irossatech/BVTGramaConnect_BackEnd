"""The backend test suite by category, with a report (audit stage S6, Jira BVTGC-17).

    python -m grama.testsuite all            every category, one pytest run
    python -m grama.testsuite permissions    one category (names: --list)
    python -m grama.testsuite --list         the categories and how many files each has
    python -m grama.testsuite --check        only check that every test file has one category

Categories live in grama/tests/suite.json. A test file in no category, in two, or listed but gone
stops the run before any test starts, so a new test file cannot be left out of the report
(grama/tests/test_suite_layout.py checks the same in every normal pytest run).

Each run writes test-reports/BE_TESTS_<scope>_<date>_<time>.md and .json (git-ignored): when and on
which commit it ran, the config's hash, counts per category, every failure, every skip with its
reason and the slowest tests. The exit code is pytest's, so 0 means everything passed.

Only the standard library is used here; the tests themselves run in a pytest child process with the
project's normal settings (pytest.ini).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "grama" / "tests" / "suite.json"
REPORTS = ROOT / "test-reports"
TEST_DIRS = ("apps", "grama")
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_files() -> set[str]:
    """Every file pytest collects (pytest.ini: python_files = tests.py test_*.py, testpaths = apps grama)."""
    found = set()
    for top in TEST_DIRS:
        for path in (ROOT / top).rglob("*.py"):
            if path.name == "tests.py" or path.name.startswith("test_"):
                if "__pycache__" not in path.parts:
                    found.add(path.relative_to(ROOT).as_posix())
    return found


def problems(config: dict) -> list[str]:
    """What is wrong with the categories: files in none, in two, or listed but missing."""
    owner: dict[str, list[str]] = defaultdict(list)
    for key, category in config["categories"].items():
        for name in category["files"]:
            owner[name].append(key)
    on_disk = test_files()
    found = [f"in no category: {name}" for name in sorted(on_disk - owner.keys())]
    found += [f"in {', '.join(keys)}: {name}" for name, keys in sorted(owner.items()) if len(keys) > 1]
    found += [f"listed but not found: {name}" for name in sorted(owner.keys() - on_disk)]
    return found


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_pytest(files: list[str], junit: Path) -> tuple[int, float]:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:logging", "-o", "junit_family=xunit1",
               f"--junitxml={junit}", *files]
    started = time.monotonic()
    code = subprocess.run(command, cwd=ROOT).returncode
    return code, time.monotonic() - started


def read_junit(junit: Path) -> list[dict]:
    """One dict per test: file, name, seconds, outcome (passed, failed, error, skipped) and message."""
    if not junit.exists():
        return []
    results = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            child = case.find(tag)
            if child is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (child.get("message") or child.text or "").strip()
                break
        results.append({
            "file": (case.get("file") or "").replace("\\", "/"),
            "name": case.get("name", ""),
            "classname": case.get("classname", ""),
            "seconds": float(case.get("time") or 0),
            "outcome": outcome,
            "message": message,
        })
    return results


def summarise(config: dict, keys: list[str], results: list[dict]) -> dict:
    category_of = {name: key for key in keys for name in config["categories"][key]["files"]}
    rows = {key: Counter() for key in keys}
    for result in results:
        key = category_of.get(result["file"])
        if key is None:
            continue
        row = rows[key]
        row["tests"] += 1
        row[result["outcome"]] += 1
        row["seconds"] += result["seconds"]
        result["category"] = key
    return rows


def short(text: str, length: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1] + "…"


def write_report(scope: str, config: dict, keys: list[str], results: list[dict], code: int, seconds: float) -> Path:
    now = datetime.now(IST)
    rows = summarise(config, keys, results)
    totals = Counter()
    for row in rows.values():
        totals.update(row)
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    meta = {
        "scope": scope,
        "started_ist": now.isoformat(timespec="seconds"),
        "commit": git("rev-parse", "--short", "HEAD") + (" (with uncommitted changes)" if dirty else ""),
        "branch": git("branch", "--show-current"),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest()[:12],
        "python": platform.python_version(),
        "pytest_exit_code": code,
        "seconds": round(seconds, 1),
        "result": "PASS" if code == 0 else "FAIL",
    }
    failures = [r for r in results if r["outcome"] in ("failed", "error")]
    skips = Counter(short(r["message"], 120) for r in results if r["outcome"] == "skipped")
    slowest = sorted(results, key=lambda r: r["seconds"], reverse=True)[:10]

    lines = [
        f"# Backend test run: {scope}",
        "",
        "| | |",
        "|---|---|",
        f"| Result | **{meta['result']}** (pytest exit code {code}) |",
        f"| Started | {now:%Y-%m-%d %H:%M:%S} IST |",
        f"| Commit | `{meta['commit']}` on `{meta['branch']}` |",
        f"| Categories file | `grama/tests/suite.json`, sha256 `{meta['config_sha256']}…` |",
        f"| Python | {meta['python']} |",
        f"| Time | {seconds / 60:.1f} minutes |",
        f"| Tests | {totals['tests']}: {totals['passed']} passed, {totals['failed']} failed, "
        f"{totals['error']} errors, {totals['skipped']} skipped |",
        "",
        "## By category",
        "",
        "| Category | Files | Tests | Passed | Failed | Errors | Skipped | Seconds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in keys:
        row, category = rows[key], config["categories"][key]
        lines.append(
            f"| {category['title']} (`{key}`) | {len(category['files'])} | {row['tests']} | {row['passed']} | "
            f"{row['failed']} | {row['error']} | {row['skipped']} | {row['seconds']:.0f} |"
        )
    lines += ["", "## Failures", ""]
    lines += [f"- `{r['file']}::{r['name']}`: {short(r['message'])}" for r in failures] or ["None."]
    lines += ["", "## Skipped, by reason", ""]
    lines += [f"- {count} × {reason or '(no reason given)'}" for reason, count in skips.most_common()] or ["None."]
    lines += ["", "## Slowest tests", "", "| Seconds | Test |", "|---:|---|"]
    lines += [f"| {r['seconds']:.1f} | `{r['file']}::{r['name']}` |" for r in slowest]
    lines.append("")

    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"BE_TESTS_{scope}_{now:%Y-%m-%d_%H-%M-%S}"
    stem.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps(
        {"meta": meta, "categories": {key: dict(rows[key]) for key in keys}, "tests": results},
        ensure_ascii=False, indent=1,
    ), encoding="utf-8")
    return stem.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    config = load_config()
    names = list(config["categories"])
    parser = argparse.ArgumentParser(prog="python -m grama.testsuite", description=__doc__.split("\n\n")[0])
    parser.add_argument("scope", nargs="?", default="all", help=f"all, or one of: {', '.join(names)}")
    parser.add_argument("--list", action="store_true", help="list the categories")
    parser.add_argument("--check", action="store_true", help="only check that every test file has one category")
    args = parser.parse_args(argv)

    if args.list:
        for key, category in config["categories"].items():
            print(f"{key:<14} {len(category['files']):>3} files  {category['title']}")
        return 0
    wrong = problems(config)
    if wrong:
        print("grama/tests/suite.json needs updating:", *wrong, sep="\n  ", file=sys.stderr)
        return 2
    if args.check:
        print(f"OK: {len(test_files())} test files, each in one of {len(names)} categories.")
        return 0
    if args.scope != "all" and args.scope not in names:
        parser.error(f"unknown category {args.scope!r}; choose all or one of: {', '.join(names)}")

    keys = names if args.scope == "all" else [args.scope]
    files = sorted(name for key in keys for name in config["categories"][key]["files"])
    with tempfile.TemporaryDirectory() as folder:
        junit = Path(folder) / "junit.xml"
        code, seconds = run_pytest(files, junit)
        results = read_junit(junit)
    report = write_report(args.scope, config, keys, results, code, seconds)
    print(f"\nReport: {report.relative_to(ROOT).as_posix()}")
    return code


if __name__ == "__main__":
    sys.exit(main())

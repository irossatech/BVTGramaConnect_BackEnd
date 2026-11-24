"""The backend test suite by category, with a report (audit stage S6, Jira BVTGC-17).

    python -m grama.testsuite all            every category, one pytest run
    python -m grama.testsuite permissions    one category (names: --list)
    python -m grama.testsuite --list         the categories and how many files each has
    python -m grama.testsuite --check        only check that every test file has one category
    python -m grama.testsuite all --coverage also measure line coverage (coverage.py, requirements.txt)

Categories live in grama/tests/suite.json. A test file in no category, in two, or listed but gone
stops the run before any test starts, so a new test file cannot be left out of the report
(grama/tests/test_suite_layout.py checks the same in every normal pytest run).

Each run writes test-reports/BE_TESTS_<scope>_<date>_<time>.md and .json (git-ignored): when and on
which commit it ran, the config's hash, counts per category, every failure, every skip with its
reason and the slowest tests. The exit code is pytest's, so 0 means everything passed.

Only the standard library is used here; the tests themselves run in a pytest child process with the
project's normal settings (pytest.ini), and grama/testsuite_plugin.py hands every outcome back as JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grama import testsuite_plugin

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


#: Measured code: the apps and the project package, without the tests, the migrations and this runner.
COVERAGE_SOURCE = "apps,grama"
COVERAGE_OMIT = "*/tests/*,*/migrations/*,grama/testsuite.py,grama/testsuite_plugin.py"


def run_pytest(files: list[str], results: Path, coverage_data: Path | None = None) -> tuple[int, float]:
    """Run pytest on `files`; grama/testsuite_plugin.py writes every outcome to `results` as JSON."""
    pytest = ["-m", "pytest", "-q", "-p", "no:logging", "-p", "grama.testsuite_plugin", *files]
    if coverage_data is None:
        command = [sys.executable, *pytest]
    else:
        command = [sys.executable, "-m", "coverage", "run", f"--data-file={coverage_data}",
                   f"--source={COVERAGE_SOURCE}", f"--omit={COVERAGE_OMIT}", *pytest]
    started = time.monotonic()
    code = subprocess.run(command, cwd=ROOT, env={**os.environ, testsuite_plugin.ENV: str(results)}).returncode
    return code, time.monotonic() - started


def read_coverage(coverage_data: Path) -> dict | None:
    """coverage's JSON report for the run, or None when coverage is missing or measured nothing."""
    out = coverage_data.with_suffix(".json")
    done = subprocess.run([sys.executable, "-m", "coverage", "json", f"--data-file={coverage_data}", "-o", str(out),
                           "-q"], cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0 or not out.exists():
        return None
    return json.loads(out.read_text(encoding="utf-8"))


def coverage_summary(report: dict) -> dict:
    """Statements and covered lines per part ("apps/posts", "grama"), the total, and the least covered files."""
    parts: dict[str, Counter] = defaultdict(Counter)
    files = []
    for name, data in report.get("files", {}).items():
        path = name.replace("\\", "/")
        segments = path.split("/")
        part = "/".join(segments[:2]) if segments[0] == "apps" and len(segments) > 2 else segments[0]
        statements = data["summary"]["num_statements"]
        covered = data["summary"]["covered_lines"]
        parts[part]["statements"] += statements
        parts[part]["covered"] += covered
        files.append({"file": path, "statements": statements, "covered": covered})

    def percent(covered: int, statements: int) -> float:
        return round(100 * covered / statements, 1) if statements else 100.0

    rows = {part: {**counts, "percent": percent(counts["covered"], counts["statements"])}
            for part, counts in sorted(parts.items()) if counts["statements"]}
    statements = sum(row["statements"] for row in rows.values())
    covered = sum(row["covered"] for row in rows.values())
    least = sorted((f for f in files if f["statements"] >= 20),
                   key=lambda f: (f["covered"] / f["statements"], -f["statements"]))[:10]
    for f in least:
        f["percent"] = percent(f["covered"], f["statements"])
    return {"parts": rows, "total": {"statements": statements, "covered": covered,
                                     "percent": percent(covered, statements)}, "least_covered": least}


def read_results(results: Path) -> list[dict]:
    """One dict per test: file, name, seconds, outcome (passed, failed, error, skipped) and message."""
    if not results.exists():
        return []
    return json.loads(results.read_text(encoding="utf-8"))


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


def write_report(scope: str, config: dict, keys: list[str], results: list[dict], code: int, seconds: float,
                 coverage: dict | None = None, started: datetime | None = None) -> Path:
    """The report's .md and .json; `started` is when the run began (its file name and "Started" line)."""
    now = started or datetime.now(IST)
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
    ]
    if coverage:
        total = coverage["total"]
        lines.append(f"| Line coverage | **{total['percent']}%** ({total['covered']:,} of {total['statements']:,} "
                     f"statements in `apps/` and `grama/`, without tests and migrations) |")
    lines += [
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
    if coverage:
        lines += ["", "## Line coverage", "",
                  "Measured with coverage.py over this run" + ("" if scope == "all" else " (one category only, so lower)")
                  + ".", "", "| Part | Statements | Covered | % |", "|---|---:|---:|---:|"]
        lines += [f"| `{part}` | {row['statements']:,} | {row['covered']:,} | {row['percent']} |"
                  for part, row in coverage["parts"].items()]
        total = coverage["total"]
        lines += [f"| **Total** | **{total['statements']:,}** | **{total['covered']:,}** | **{total['percent']}** |",
                  "", "Least covered files (20 statements or more):", "", "| File | Statements | % |", "|---|---:|---:|"]
        lines += [f"| `{f['file']}` | {f['statements']} | {f['percent']} |" for f in coverage["least_covered"]]
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
        {"meta": meta, "categories": {key: dict(rows[key]) for key in keys}, "coverage": coverage, "tests": results},
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
    parser.add_argument("--coverage", action="store_true", help="also measure line coverage (coverage.py)")
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
    coverage = None
    started = datetime.now(IST)
    with tempfile.TemporaryDirectory() as folder:
        outcomes = Path(folder) / "results.json"
        data = Path(folder) / "coverage.data" if args.coverage else None
        code, seconds = run_pytest(files, outcomes, data)
        results = read_results(outcomes)
        if data is not None:
            measured = read_coverage(data)
            if measured is None:
                print("Coverage was not measured: is coverage installed (requirements.txt)?", file=sys.stderr)
            else:
                coverage = coverage_summary(measured)
    report = write_report(args.scope, config, keys, results, code, seconds, coverage, started)
    print(f"\nReport: {report.relative_to(ROOT).as_posix()}")
    return code


if __name__ == "__main__":
    sys.exit(main())

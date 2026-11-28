"""Load, stress, spike and soak tests against STAGING, with reports (audit stage S6, BVTGC-17).

    python -m grama.perftest load      each user count of the "load" profile in turn (1, 5, 10, 25, 50)
    python -m grama.perftest stress    more users each round until the error rate or P95 crosses the limit
    python -m grama.perftest spike     0 to 50 users at once; how long until P95 is back within the SLA
    python -m grama.perftest soak      10 minutes sustained; error drift, server memory and DB connections
    python -m grama.perftest load --check     only check the config and the target, run nothing

Settings: deploy/loadtest/profiles.json (profiles, SLA, refused hosts) and .env.test (git-ignored; copy
.env.test.example): LOADTEST_BASE_URL, optionally a staging official and a metrics address. Values in
the environment win over .env.test. The runner refuses to start without a base URL, or against a host
listed in refused_hosts (production and the qa server).

Each run writes test-reports/<kind>-report-<YYYY-MM-DD_HH-mm-ss>.json and .html (git-ignored, never
overwritten): start and end, duration, the base URL, Python and Django versions, this machine's CPU and
memory, the config's hash, the commit, and per step and per endpoint the requests, RPS, error rate,
average, minimum, maximum, P95 and P99, the SLA result, the failures, and for stress the breaking point,
for spike the recovery time, for soak the drift. The test dashboard (python -m grama.testsuite) shows the
newest of each. Locust runs in a child process (requirements.txt); this file uses only the standard library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "deploy" / "loadtest" / "profiles.json"
LOCUSTFILE = ROOT / "deploy" / "loadtest" / "locustfile.py"
ENV_FILE = ROOT / ".env.test"
REPORTS = ROOT / "test-reports"
IST = timezone(timedelta(hours=5, minutes=30), "IST")
KINDS = ("load", "stress", "spike", "soak")


# --- Configuration --------------------------------------------------------------------------------------


def load_config() -> dict:
    return json.loads(PROFILES.read_text(encoding="utf-8"))


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file; comments and blank lines skipped, quotes taken off."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def settings(environ: dict[str, str] | None = None, env_file: Path = ENV_FILE) -> dict[str, str]:
    """.env.test, then the environment on top (it wins)."""
    merged = read_env_file(env_file)
    merged.update({k: v for k, v in (environ if environ is not None else os.environ).items() if k.startswith("LOADTEST_")})
    return merged


def problems(config: dict, kind: str, values: dict[str, str]) -> list[str]:
    """Everything that stops a run, as readable lines; empty when it may start."""
    found = []
    if kind not in config.get("profiles", {}):
        found.append(f"no profile {kind!r} in deploy/loadtest/profiles.json")
    for key in config.get("required_env", []):
        if not values.get(key):
            found.append(f"{key} is not set (.env.test or the environment; see .env.test.example)")
    base = values.get("LOADTEST_BASE_URL", "")
    if base:
        parsed = urlparse(base)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            found.append(f"LOADTEST_BASE_URL must be http(s)://host, not {base!r}")
        refused = config.get("refused_hosts", {})
        if host in refused:
            found.append(f"refusing {host}: {refused[host]}. Load tests run against staging only")
    if bool(values.get("LOADTEST_OFFICIAL_USERNAME")) != bool(values.get("LOADTEST_OFFICIAL_PASSWORD")):
        found.append("set both LOADTEST_OFFICIAL_USERNAME and LOADTEST_OFFICIAL_PASSWORD, or neither")
    metrics = values.get("LOADTEST_METRICS_URL", "")
    if metrics and urlparse(metrics).scheme not in ("http", "https"):
        found.append("LOADTEST_METRICS_URL must start with http:// or https://")
    return found


def config_hash() -> str:
    digest = hashlib.sha256()
    for path in (PROFILES, LOCUSTFILE):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


# --- Locust's CSV output --------------------------------------------------------------------------------


def _number(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_stats(path: Path) -> dict:
    """Locust's <prefix>_stats.csv: per endpoint and in total (the "Aggregated" row)."""
    endpoints, total = {}, {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            requests = int(_number(row.get("Request Count")))
            failures = int(_number(row.get("Failure Count")))
            figures = {
                "requests": requests, "failures": failures,
                "error_percent": round(100 * failures / requests, 2) if requests else 0.0,
                "avg_ms": round(_number(row.get("Average Response Time")), 1),
                "min_ms": round(_number(row.get("Min Response Time")), 1),
                "max_ms": round(_number(row.get("Max Response Time")), 1),
                "p95_ms": round(_number(row.get("95%")), 1), "p99_ms": round(_number(row.get("99%")), 1),
                "rps": round(_number(row.get("Requests/s")), 2),
            }
            if row.get("Name") == "Aggregated":
                total = figures
            else:
                endpoints[row.get("Name", "?")] = figures
    return {"endpoints": endpoints, "total": total}


def parse_failures(path: Path, limit: int = 10) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [{"endpoint": r.get("Name"), "error": r.get("Error"), "count": int(_number(r.get("Occurrences")))}
                for r in csv.DictReader(handle)]
    return sorted(rows, key=lambda r: r["count"], reverse=True)[:limit]


def parse_history(path: Path) -> list[dict]:
    """The "Aggregated" rows of <prefix>_stats_history.csv: one per second, with users, RPS and P95."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [{"t": int(_number(r.get("Timestamp"))), "users": int(_number(r.get("User Count"))),
                 "rps": _number(r.get("Requests/s")), "failures_s": _number(r.get("Failures/s")),
                 "p95_ms": _number(r.get("95%"))}
                for r in csv.DictReader(handle) if r.get("Name") == "Aggregated"]


def sla_misses(stats: dict, sla: dict) -> list[str]:
    """Each endpoint (and the total) against its SLA: what it missed, as readable lines."""
    misses = []
    rows = {**stats["endpoints"], "total": stats["total"]}
    for name, figures in rows.items():
        if not figures.get("requests"):
            continue
        limits = {**sla["default"], **sla.get("endpoints", {}).get(name, {})}
        for key in ("p95_ms", "p99_ms", "error_percent"):
            if key in limits and figures.get(key, 0) > limits[key]:
                misses.append(f"{name}: {key} {figures[key]} > {limits[key]}")
    return misses


def recovery_seconds(history: list[dict], p95_limit: float, users: int) -> int | None:
    """Seconds from when all `users` were running until P95 first came back within the limit."""
    full = next((row["t"] for row in history if row["users"] >= users), None)
    if full is None:
        return None
    for row in history:
        if row["t"] >= full and 0 < row["p95_ms"] <= p95_limit:
            return row["t"] - full
    return None


def drift(history: list[dict], minutes: int = 1) -> dict:
    """Soak: failures per second and P95 in the first and the last `minutes`."""
    if not history:
        return {}
    start, end = history[0]["t"], history[-1]["t"]
    first = [r for r in history if r["t"] < start + 60 * minutes]
    last = [r for r in history if r["t"] > end - 60 * minutes]

    def mean(rows, key):
        return round(sum(r[key] for r in rows) / len(rows), 2) if rows else 0.0

    return {"first_failures_s": mean(first, "failures_s"), "last_failures_s": mean(last, "failures_s"),
            "first_p95_ms": mean(first, "p95_ms"), "last_p95_ms": mean(last, "p95_ms")}


def first_failing_component(failures: list[dict]) -> str | None:
    """A guess from the commonest failure: Gunicorn or Nginx timeouts, the database, or the app."""
    if not failures:
        return None
    text = (failures[0].get("error") or "").lower()
    if "504" in text or "502" in text or "timeout" in text or "timed out" in text:
        return "web server (Gunicorn workers or Nginx upstream timeouts): " + failures[0]["error"]
    if "too many connections" in text or "database" in text or "connection" in text:
        return "database connections: " + failures[0]["error"]
    if "429" in text:
        return "rate limits (Nginx limit_req or the app's throttles): " + failures[0]["error"]
    return f"{failures[0]['endpoint']}: {failures[0]['error']}"


# --- Running Locust ---------------------------------------------------------------------------------------


class MetricsSampler:
    """Reads LOADTEST_METRICS_URL every `every` seconds while a step runs (when it is set)."""

    def __init__(self, url: str, every: float = 5.0):
        self.url, self.every, self.samples = url, every, []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                # The scheme is checked to be http(s) in problems() before any run.
                with urllib.request.urlopen(self.url, timeout=4) as answer:  # nosec B310
                    sample = json.loads(answer.read().decode("utf-8"))
                self.samples.append({"t": round(time.time()), **{k: sample.get(k) for k in ("cpu_percent", "memory_mb", "db_connections")}})
            except (OSError, ValueError):
                self.samples.append({"t": round(time.time()), "error": "metrics address did not answer"})
            self._stop.wait(self.every)

    def __enter__(self):
        if self.url:
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self.url:
            self._thread.join(timeout=10)


def run_step(values: dict, users: int, spawn_rate: int, duration_s: int, folder: Path, name: str) -> dict:
    prefix = folder / name
    command = [sys.executable, "-m", "locust", "-f", str(LOCUSTFILE), "--headless", "--only-summary",
               "--host", values["LOADTEST_BASE_URL"], "-u", str(users), "-r", str(spawn_rate),
               "-t", f"{duration_s}s", "--csv", str(prefix), "--csv-full-history", "--exit-code-on-error", "0"]
    env = {**os.environ, **{k: v for k, v in values.items() if k.startswith("LOADTEST_")}}
    started = time.monotonic()
    with MetricsSampler(values.get("LOADTEST_METRICS_URL", "")) as sampler:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    stats_file = Path(f"{prefix}_stats.csv")
    if done.returncode != 0 or not stats_file.exists():
        raise RuntimeError(f"Locust did not finish ({done.returncode}): {done.stderr[-800:]}")
    return {"users": users, "seconds": round(time.monotonic() - started, 1), **parse_stats(stats_file),
            "failures": parse_failures(Path(f"{prefix}_failures.csv")),
            "history": parse_history(Path(f"{prefix}_stats_history.csv")), "server": sampler.samples}


def run(kind: str, config: dict, values: dict, folder: Path) -> dict:
    profile, sla = config["profiles"][kind], config["sla"]
    out = {"steps": []}

    def step(users, spawn_rate, duration_s, name):
        result = run_step(values, users, spawn_rate, duration_s, folder, name)
        misses = sla_misses(result, sla)
        result.update(sla_met=not misses, sla_misses=misses)
        out["steps"].append(result)
        return result

    if kind == "load":
        for users in profile["users"]:
            step(users, min(users, profile["spawn_rate"]), profile["duration_s"], f"load_{users}")
    elif kind == "stress":
        users, limits = profile["start"], profile["stop_when"]
        while users <= profile["max"]:
            result = step(users, profile["spawn_rate"], profile["duration_s"], f"stress_{users}")
            total = result["total"]
            if total.get("error_percent", 0) > limits["error_percent"] or total.get("p95_ms", 0) > limits["p95_ms"]:
                out["breaking_point"] = f"{users} users (error rate {total.get('error_percent')}%, P95 {total.get('p95_ms')} ms)"
                out["first_failing_component"] = first_failing_component(result["failures"])
                break
            users += profile["step"]
        else:
            out["breaking_point"] = f"not reached up to {profile['max']} users"
    elif kind == "spike":
        result = step(profile["users"], profile["spawn_rate"], profile["duration_s"], "spike")
        out["recovery_s"] = recovery_seconds(result["history"], sla["default"]["p95_ms"], profile["users"])
    elif kind == "soak":
        result = step(profile["users"], profile["spawn_rate"], profile["duration_s"], "soak")
        out["drift"] = drift(result["history"])
        memory = [s["memory_mb"] for s in result["server"] if s.get("memory_mb") is not None]
        out["memory_growth_mb"] = round(memory[-1] - memory[0], 1) if len(memory) > 1 else None
    return out


# --- Reports ----------------------------------------------------------------------------------------------


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_report(kind: str, values: dict, outcome: dict, started: datetime, seconds: float) -> Path:
    from grama.testsuite import django_version, machine

    stamp = f"{started:%Y-%m-%d_%H-%M-%S}"
    for step in outcome["steps"]:
        step.pop("history", None)  # one row a second: kept out of the report, used above
    report = {
        "kind": kind, "environment": "staging", "base_url": values["LOADTEST_BASE_URL"],
        "started_ist": started.isoformat(timespec="seconds"),
        "ended_ist": (started + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
        "seconds": round(seconds, 1), "python": sys.version.split()[0], "django": django_version(),
        "load_generator": machine(), "config_sha256": config_hash(),
        "commit": git("rev-parse", "--short", "HEAD"), "branch": git("branch", "--show-current"),
        "official_mix": bool(values.get("LOADTEST_OFFICIAL_USERNAME")),
        "server_metrics": bool(values.get("LOADTEST_METRICS_URL")),
        **outcome,
    }
    REPORTS.mkdir(exist_ok=True)
    json_path = REPORTS / f"{kind}-report-{stamp}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    json_path.with_suffix(".html").write_text(render_html(report), encoding="utf-8")
    return json_path


def render_html(report: dict) -> str:
    from grama.testdashboard import STYLE

    def cell(value) -> str:
        return html.escape("-" if value is None else str(value))

    rows = []
    for step in report["steps"]:
        for name, figures in [("total", step["total"]), *sorted(step["endpoints"].items())]:
            rows.append(
                f"<tr><td class='n'>{step['users']}</td><td>{cell(name)}</td><td class='n'>{figures.get('requests')}</td>"
                f"<td class='n'>{figures.get('rps')}</td><td class='n'>{figures.get('error_percent')}</td>"
                f"<td class='n'>{figures.get('avg_ms')}</td><td class='n'>{figures.get('min_ms')}</td>"
                f"<td class='n'>{figures.get('max_ms')}</td><td class='n'>{figures.get('p95_ms')}</td>"
                f"<td class='n'>{figures.get('p99_ms')}</td></tr>")
    misses = [m for step in report["steps"] for m in step.get("sla_misses", [])]
    failures = [f for step in report["steps"] for f in step.get("failures", [])]
    return "\n".join([
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{cell(report['kind'].title())} test</title><style>{STYLE}</style></head><body><main>",
        f"<h1>{cell(report['kind'].title())} test: {cell(report['base_url'])}</h1>",
        f"<p class='muted'>{cell(report['started_ist'])} to {cell(report['ended_ist'])} · commit <code>{cell(report['commit'])}</code>"
        f" · Python {cell(report['python'])} · Django {cell(report['django'])} · load generator {cell(report['load_generator'])}"
        f" · config sha256 <code>{cell(report['config_sha256'])}</code></p>",
        "<div class='cards'>",
        f"<div class='card'>Breaking point<b>{cell(report.get('breaking_point'))}</b></div>",
        f"<div class='card'>Recovery after the spike<b>{cell(report.get('recovery_s'))} s</b></div>",
        f"<div class='card'>SLA<b class='{'LOW' if not misses else 'HIGH'}'>{'met' if not misses else 'missed'}</b></div>",
        "</div>",
        "<h2>Per step and endpoint</h2><div class='wrap'><table><tr><th class='n'>Users</th><th>Endpoint</th>"
        "<th class='n'>Requests</th><th class='n'>RPS</th><th class='n'>Errors %</th><th class='n'>Avg ms</th>"
        "<th class='n'>Min</th><th class='n'>Max</th><th class='n'>P95</th><th class='n'>P99</th></tr>",
        *rows, "</table></div>",
        "<h2>SLA misses</h2>" + ("<ul>" + "".join(f"<li>{cell(m)}</li>" for m in misses) + "</ul>" if misses else "<p>None.</p>"),
        "<h2>Failures</h2>" + ("<ul>" + "".join(f"<li>{cell(f['endpoint'])}: {cell(f['error'])} × {f['count']}</li>"
                                               for f in failures) + "</ul>" if failures else "<p>None.</p>"),
        f"<h2>First failing component</h2><p>{cell(report.get('first_failing_component'))}</p>",
        f"<h2>Soak drift</h2><p>{cell(report.get('drift'))}; memory growth {cell(report.get('memory_growth_mb'))} MB</p>",
        "</main></body></html>",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grama.perftest", description=__doc__.split("\n\n")[0])
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--host", help="the staging base URL, instead of LOADTEST_BASE_URL")
    parser.add_argument("--check", action="store_true", help="only check the config and the target")
    parser.add_argument("--smoke", action="store_true",
                        help="a short trial of the runner itself: 2 users for 20 s, whatever the profile says")
    args = parser.parse_args(argv)

    config = load_config()
    if args.smoke:
        profile = config["profiles"][args.kind]
        profile.update(duration_s=20, spawn_rate=2, users=[1, 2] if args.kind == "load" else 2, start=2, step=2, max=4)
    values = settings()
    if args.host:
        values["LOADTEST_BASE_URL"] = args.host
    wrong = problems(config, args.kind, values)
    if wrong:
        print("Not running:", *wrong, sep="\n  ", file=sys.stderr)
        return 2
    if args.check:
        print(f"OK: {args.kind} against {values['LOADTEST_BASE_URL']} (config sha256 {config_hash()}).")
        return 0
    started = datetime.now(IST)
    clock = time.monotonic()
    with tempfile.TemporaryDirectory() as folder:
        outcome = run(args.kind, config, values, Path(folder))
    path = write_report(args.kind, values, outcome, started, time.monotonic() - clock)
    print(f"Report: {path.relative_to(ROOT).as_posix()} (and .html)")
    return 0 if all(step["sla_met"] for step in outcome["steps"]) else 1


if __name__ == "__main__":
    sys.exit(main())

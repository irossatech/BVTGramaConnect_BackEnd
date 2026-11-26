"""The backend test dashboard and the HTML copy of a run's report (audit stage S6, BVTGC-17).

grama.testsuite calls write_dashboard() after a run. It writes
test-reports/backend-test-dashboard-<YYYY-MM-DD_HH-mm-ss>.html (git-ignored, never overwritten): the
health score (0-100), pass and fail per category, line coverage, API coverage (the API's methods and
routes the tests called), RBAC matrix coverage (those grama/tests/config/rbac-matrix.json checks for
every role), the known bugs (xfail tests with their finding IDs), the latest load-test figures when there
are any (grama.perftest), the risk class and links to every report of the run.

The score is worked out from grama/tests/suite.json, "dashboard":
  tests          parts.tests x the weighted pass rate of the categories (a known bug counts as not passed)
  line coverage  parts.line_coverage x min(1, coverage / line_coverage_target)
  API coverage   parts.api_coverage x min(1, API coverage / api_coverage_target)
  RBAC coverage  parts.rbac_coverage x min(1, RBAC coverage / api_coverage_target)
Without --coverage the line coverage part is left out and the rest is scaled to 100 (the page says so).
Risk: LOW from risk.low_at_least, MEDIUM from risk.medium_at_least, else HIGH; a failed test makes it at
least MEDIUM. Only the standard library is used.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

PERF_PREFIXES = ("load", "stress", "spike", "soak")


def percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def api_figures(routes: dict) -> dict:
    """API and RBAC coverage from the plugin's routes file: only /api/v1/ methods count."""
    def api(rows):
        return {tuple(row) for row in rows if row[1].startswith("api/v1/")}

    every, called, rbac = api(routes.get("all", [])), api(routes.get("called", [])), api(routes.get("rbac", []))
    return {
        "endpoints": len(every), "called": len(called), "rbac": len(rbac),
        "api_percent": percent(len(called), len(every)), "rbac_percent": percent(len(rbac), len(every)),
        "not_called": sorted(f"{method} /{route}" for method, route in every - called),
    }


def pass_rate(row: dict) -> float:
    counted = row.get("passed", 0) + row.get("failed", 0) + row.get("error", 0) + row.get("xfailed", 0)
    return row.get("passed", 0) / counted if counted else 1.0


def health(config: dict, rows: dict[str, dict], coverage: dict | None, api: dict) -> dict:
    """The score, its parts and the risk class (see the module docstring)."""
    board = config["dashboard"]
    parts, weights = board["parts"], board["weights"]
    used = {key: weights.get(key, 0) for key in rows}
    total_weight = sum(used.values()) or 1
    tests = sum(pass_rate(rows[key]) * weight for key, weight in used.items()) / total_weight
    line = (coverage or {}).get("total", {}).get("percent")
    got = {
        "tests": parts["tests"] * tests,
        "line_coverage": parts["line_coverage"] * min(1.0, (line or 0) / board["line_coverage_target"]),
        "api_coverage": parts["api_coverage"] * min(1.0, api["api_percent"] / board["api_coverage_target"]),
        "rbac_coverage": parts["rbac_coverage"] * min(1.0, api["rbac_percent"] / board["api_coverage_target"]),
    }
    if line is None:  # not measured in this run (no --coverage): score the other parts out of 100
        got.pop("line_coverage")
        score = round(sum(got.values()) * 100 / (100 - parts["line_coverage"]), 1)
    else:
        score = round(sum(got.values()), 1)
    failed = sum(row.get("failed", 0) + row.get("error", 0) for row in rows.values())
    if score >= board["risk"]["low_at_least"] and not failed:
        risk = "LOW"
    elif score >= board["risk"]["medium_at_least"]:
        risk = "MEDIUM"
    else:
        risk = "HIGH"
    return {"score": score, "parts": {key: round(value, 1) for key, value in got.items()}, "risk": risk,
            "line_measured": line is not None}


def latest_perf_reports(reports: Path) -> list[dict]:
    """The newest grama.perftest report of each kind (load, stress, spike, soak), when there are any."""
    found = []
    for kind in PERF_PREFIXES:
        files = sorted(reports.glob(f"{kind}-report-*.json"))
        if files:
            data = json.loads(files[-1].read_text(encoding="utf-8"))
            data["_file"] = files[-1].name
            found.append(data)
    return found


def bar(label: str, value: float, good: bool, width: int = 260) -> str:
    filled = max(0, min(width, int(width * value / 100)))
    colour = "var(--good)" if good else "var(--bad)"
    return (f'<div class="bar"><span class="lbl">{html.escape(label)}</span>'
            f'<svg width="{width}" height="14" role="img" aria-label="{value}%"><rect width="{width}" height="14" '
            f'fill="var(--track)"/><rect width="{filled}" height="14" fill="{colour}"/></svg>'
            f'<span class="val">{value:.1f}%</span></div>')


STYLE = """
:root{--bg:#fff;--fg:#1d2433;--muted:#5b6475;--card:#f5f7fa;--line:#dfe3ea;--good:#1f8a3b;--bad:#c0392b;--warn:#b7791f;--track:#e5e8ee}
@media (prefers-color-scheme: dark){:root{--bg:#12151c;--fg:#e6e9ef;--muted:#9aa3b2;--card:#1b1f29;--line:#2c3240;--track:#2a2f3b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:24px 0 8px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.card b{display:block;font-size:24px}.muted{color:var(--muted)}
.LOW{color:var(--good)}.MEDIUM{color:var(--warn)}.HIGH{color:var(--bad)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left}
td.n,th.n{text-align:right}.wrap{overflow-x:auto}
.bar{display:flex;align-items:center;gap:8px;margin:4px 0}.bar .lbl{width:220px;flex:none}.bar .val{width:60px}
code{font-size:13px}
"""


def write_dashboard(*, reports: Path, stamp: str, meta: dict, config: dict, keys: list[str], rows: dict,
                    coverage: dict | None, routes: dict, results: list[dict], links: list[str]) -> tuple[Path, dict]:
    api = api_figures(routes)
    score = health(config, {key: dict(rows[key]) for key in keys}, coverage, api)
    known_bugs = [r for r in results if r["outcome"] == "xfailed"]
    failures = [r for r in results if r["outcome"] in ("failed", "error")]
    perf = latest_perf_reports(reports)
    line = (coverage or {}).get("total", {}).get("percent")

    def cell(text) -> str:
        return html.escape(str(text))

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Backend test dashboard</title><style>{STYLE}</style></head><body><main>",
        "<h1>BVT Grama Connect backend: test dashboard</h1>",
        f"<p class='muted'>Started {cell(meta['started_ist'])} · commit <code>{cell(meta['commit'])}</code> on "
        f"<code>{cell(meta['branch'])}</code> · Python {cell(meta['python'])} · Django {cell(meta.get('django', '?'))} · "
        f"{cell(meta.get('machine', ''))} · config sha256 <code>{cell(meta['config_sha256'])}</code></p>",
        "<div class='cards'>",
        f"<div class='card'>Health score<b>{score['score']}</b><span class='muted'>out of 100"
        f"{'' if score['line_measured'] else ', without line coverage'}</span></div>",
        f"<div class='card'>Risk<b class='{score['risk']}'>{score['risk']}</b></div>",
        f"<div class='card'>Result<b class='{'LOW' if meta['result'] == 'PASS' else 'HIGH'}'>{meta['result']}</b>"
        f"<span class='muted'>{len(failures)} failed, {len(known_bugs)} known bugs</span></div>",
        f"<div class='card'>Line coverage<b>{'-' if line is None else f'{line}%'}</b>"
        f"<span class='muted'>{'' if line is not None else 'run with --coverage'}</span></div>",
        f"<div class='card'>API coverage<b>{api['api_percent']}%</b><span class='muted'>{api['called']} of "
        f"{api['endpoints']} methods called</span></div>",
        f"<div class='card'>RBAC matrix<b>{api['rbac_percent']}%</b><span class='muted'>{api['rbac']} methods x every role</span></div>",
        "</div>",
        "<h2>Score</h2><div class='wrap'><table><tr><th>Part</th><th class='n'>Points</th><th class='n'>Of</th></tr>",
    ]
    for key, value in score["parts"].items():  # line coverage is missing when it was not measured
        parts.append(f"<tr><td>{cell(key.replace('_', ' '))}</td><td class='n'>{value}</td>"
                     f"<td class='n'>{config['dashboard']['parts'][key]}</td></tr>")
    parts.append("</table></div><h2>By category</h2>")
    for key in keys:
        parts.append(bar(config["categories"][key]["title"], 100 * pass_rate(dict(rows[key])),
                         rows[key].get("failed", 0) + rows[key].get("error", 0) == 0))
    parts.append("<div class='wrap'><table><tr><th>Category</th><th class='n'>Tests</th><th class='n'>Passed</th>"
                 "<th class='n'>Failed</th><th class='n'>Errors</th><th class='n'>Known bugs</th><th class='n'>Skipped</th>"
                 "<th class='n'>Seconds</th><th>Result</th></tr>")
    for key in keys:
        row = rows[key]
        ok = row.get("failed", 0) + row.get("error", 0) == 0
        parts.append(
            f"<tr><td>{cell(config['categories'][key]['title'])} <code>{key}</code></td><td class='n'>{row.get('tests', 0)}</td>"
            f"<td class='n'>{row.get('passed', 0)}</td><td class='n'>{row.get('failed', 0)}</td><td class='n'>{row.get('error', 0)}</td>"
            f"<td class='n'>{row.get('xfailed', 0)}</td><td class='n'>{row.get('skipped', 0)}</td>"
            f"<td class='n'>{row.get('seconds', 0):.0f}</td><td class='{'LOW' if ok else 'HIGH'}'>{'PASS' if ok else 'FAIL'}</td></tr>")
    parts.append("</table></div>")

    parts.append("<h2>Known bugs (xfail, linked to their finding)</h2>")
    if known_bugs:
        parts.append("<ul>" + "".join(f"<li><code>{cell(r['file'])}::{cell(r['name'])}</code>: {cell(r['message'])}</li>"
                                      for r in known_bugs) + "</ul>")
    else:
        parts.append("<p>None.</p>")
    parts.append("<h2>Failures</h2>")
    parts.append("<ul>" + "".join(f"<li><code>{cell(r['file'])}::{cell(r['name'])}</code>: {cell(r['message'])}</li>"
                                  for r in failures) + "</ul>" if failures else "<p>None.</p>")

    parts.append("<h2>Load, stress, spike and soak</h2>")
    if perf:
        parts.append("<div class='wrap'><table><tr><th>Run</th><th>Base URL</th><th class='n'>Users</th><th class='n'>Requests</th>"
                     "<th class='n'>RPS</th><th class='n'>Errors %</th><th class='n'>P95 ms</th><th class='n'>P99 ms</th>"
                     "<th>SLA</th><th>Breaking point</th></tr>")
        for report in perf:
            for step in report.get("steps", []):
                total = step.get("total", {})
                parts.append(
                    f"<tr><td><a href='{cell(report['_file'].replace('.json', '.html'))}'>{cell(report['kind'])}</a></td>"
                    f"<td>{cell(report.get('base_url', ''))}</td><td class='n'>{step.get('users', '')}</td>"
                    f"<td class='n'>{total.get('requests', '')}</td><td class='n'>{total.get('rps', '')}</td>"
                    f"<td class='n'>{total.get('error_percent', '')}</td><td class='n'>{total.get('p95_ms', '')}</td>"
                    f"<td class='n'>{total.get('p99_ms', '')}</td><td>{'met' if step.get('sla_met') else 'missed'}</td>"
                    f"<td>{cell(report.get('breaking_point') or '-')}</td></tr>")
        parts.append("</table></div>")
    else:
        parts.append("<p class='muted'>Not run yet: they need the staging server (BVTGC-38) and the expected number "
                     "of users (BVTGC-37). <code>python -m grama.perftest --help</code></p>")

    parts.append("<h2>API methods no test called</h2>")
    if api["not_called"]:
        parts.append("<details><summary>" + f"{len(api['not_called'])} of {api['endpoints']}" + "</summary><ul>"
                     + "".join(f"<li><code>{cell(item)}</code></li>" for item in api["not_called"]) + "</ul></details>")
    else:
        parts.append("<p>None.</p>")
    parts.append("<h2>Reports of this run</h2><ul>" + "".join(f"<li><a href='{cell(link)}'>{cell(link)}</a></li>" for link in links)
                 + "".join(f"<li><a href='{cell(r['_file'].replace('.json', '.html'))}'>{cell(r['_file'])}</a></li>" for r in perf)
                 + "</ul></main></body></html>")

    out = reports / f"backend-test-dashboard-{stamp}.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out, {"health": score, "api": {k: v for k, v in api.items() if k != "not_called"}}


def markdown_to_html(markdown: str, title: str) -> str:
    """A plain HTML copy of a run's Markdown report: headings, tables, lists and paragraphs."""
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body><main>"]
    in_table = in_list = False
    for line in markdown.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<div class='wrap'><table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{html.escape(c).replace('**', '')}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table></div>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("#"):
            level = min(3, len(line) - len(line.lstrip("#")))
            out.append(f"<h{level}>{html.escape(line.lstrip('#').strip())}</h{level}>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    out.append("</table></div>" if in_table else "")
    out.append("</ul>" if in_list else "")
    out.append("</main></body></html>")
    return "\n".join(out)

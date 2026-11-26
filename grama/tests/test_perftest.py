"""The load-test runner checks its target and reads Locust's figures right (audit stage S6, BVTGC-17).

The runs themselves need the staging server (BVTGC-38); these tests check, without any network:
production and qa are refused, a missing base URL stops the run before Locust starts, .env.test and the
environment are read, Locust's CSV output becomes the report's figures, the SLA is applied per endpoint,
stress stops at its breaking point, spike recovery and soak drift are measured, and the locustfile names
no host, user or password.
"""

from __future__ import annotations

import re

import pytest

from grama import perftest

CONFIG = perftest.load_config()
STAGING = {"LOADTEST_BASE_URL": "https://staging.example.org"}


@pytest.mark.parametrize("host", ["gramaconnect.com", "www.gramaconnect.com", "dev.bvtgramaconnect.com", "dev.gramaconnect.com"])
def test_production_and_qa_are_refused(host):
    found = perftest.problems(CONFIG, "load", {"LOADTEST_BASE_URL": f"https://{host}"})
    assert any(f"refusing {host}" in line for line in found)


@pytest.mark.parametrize("values, message", [
    ({}, "LOADTEST_BASE_URL is not set"),
    ({"LOADTEST_BASE_URL": "staging.example.org"}, "must be http(s)://host"),
    ({"LOADTEST_BASE_URL": "ftp://staging.example.org"}, "must be http(s)://host"),
    ({**STAGING, "LOADTEST_OFFICIAL_USERNAME": "loadtest.va"}, "set both"),
    ({**STAGING, "LOADTEST_METRICS_URL": "file:///etc/passwd"}, "LOADTEST_METRICS_URL must start"),
])
def test_a_bad_config_stops_the_run(values, message):
    assert any(message in line for line in perftest.problems(CONFIG, "load", values))


def test_staging_with_everything_set_may_run():
    values = {**STAGING, "LOADTEST_OFFICIAL_USERNAME": "loadtest.va", "LOADTEST_OFFICIAL_PASSWORD": "x",
              "LOADTEST_METRICS_URL": "https://staging.example.org/metrics"}
    for kind in perftest.KINDS:
        assert perftest.problems(CONFIG, kind, values) == []


def test_the_runner_starts_nothing_against_production(monkeypatch, capsys):
    monkeypatch.setattr(perftest, "settings", lambda: {"LOADTEST_BASE_URL": "https://www.gramaconnect.com"})
    monkeypatch.setattr(perftest, "run", lambda *a, **k: pytest.fail("Locust must not start"))
    assert perftest.main(["load"]) == 2
    assert "production" in capsys.readouterr().err


def test_check_only_checks(monkeypatch, capsys):
    monkeypatch.setattr(perftest, "settings", lambda: dict(STAGING))
    monkeypatch.setattr(perftest, "run", lambda *a, **k: pytest.fail("--check must not run Locust"))
    assert perftest.main(["soak", "--check"]) == 0
    assert "OK: soak against https://staging.example.org" in capsys.readouterr().out


def test_env_file_then_environment(tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text("# comment\nLOADTEST_BASE_URL='https://staging.example.org'\nLOADTEST_OFFICIAL_USERNAME=a\n\nNOT_OURS=1\n",
                        encoding="utf-8")
    values = perftest.settings({"LOADTEST_OFFICIAL_USERNAME": "b", "PATH": "/bin"}, env_file)
    assert values["LOADTEST_BASE_URL"] == "https://staging.example.org"
    assert values["LOADTEST_OFFICIAL_USERNAME"] == "b"  # the environment wins
    assert "PATH" not in values


STATS = """Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%
GET,feed first page,1000,5,120,140.5,40,2200,5000,16.6,0.08,120,140,160,180,300,650,900,1200,2000,2200,2200
GET,feed post detail,400,0,80,90,30,700,3000,6.6,0,80,90,100,110,150,200,300,400,700,700,700
,Aggregated,1400,5,100,126.1,30,2200,4400,23.2,0.08,100,120,140,150,250,550,800,1100,2000,2200,2200
"""


def test_locust_figures_become_the_reports_figures(tmp_path):
    path = tmp_path / "run_stats.csv"
    path.write_text(STATS, encoding="utf-8")
    stats = perftest.parse_stats(path)
    assert stats["total"] == {"requests": 1400, "failures": 5, "error_percent": 0.36, "avg_ms": 126.1, "min_ms": 30.0,
                              "max_ms": 2200.0, "p95_ms": 550.0, "p99_ms": 1100.0, "rps": 23.2}
    assert stats["endpoints"]["feed first page"]["p95_ms"] == 650.0
    # The feed's own SLA (600 ms) is missed though the default (800 ms) would pass it.
    assert perftest.sla_misses(stats, CONFIG["sla"]) == ["feed first page: p95_ms 650.0 > 600"]


def test_stress_stops_at_the_breaking_point(monkeypatch, tmp_path):
    def fake_step(values, users, spawn_rate, duration_s, folder, name):
        errors = 0.5 if users < 100 else 12.0
        total = {"requests": 1000, "error_percent": errors, "p95_ms": 400 if users < 100 else 4000}
        failures = [{"endpoint": "feed first page", "error": "HTTPError('504 Server Error: Gateway Time-out')", "count": 90}]
        return {"users": users, "endpoints": {}, "total": total, "failures": failures if users >= 100 else [], "server": []}

    monkeypatch.setattr(perftest, "run_step", fake_step)
    outcome = perftest.run("stress", CONFIG, STAGING, tmp_path)
    assert [step["users"] for step in outcome["steps"]] == [50, 75, 100]
    assert outcome["breaking_point"].startswith("100 users")
    assert outcome["first_failing_component"].startswith("web server")


def test_spike_recovery_and_soak_drift():
    history = [{"t": 100 + s, "users": min(50, 10 * s), "rps": 20, "failures_s": 0, "p95_ms": p}
               for s, p in enumerate([300, 900, 2500, 3000, 2000, 1200, 700, 600, 500])]
    assert perftest.recovery_seconds(history, 800, 50) == 1  # all 50 at t=105 (P95 1200), back under 800 at t=106
    long_run = [{"t": t, "users": 25, "rps": 10, "failures_s": 0.0 if t < 540 else 0.5, "p95_ms": 300 if t < 540 else 500}
                for t in range(0, 600)]
    assert perftest.drift(long_run) == {"first_failures_s": 0.0, "last_failures_s": 0.5, "first_p95_ms": 300.0,
                                        "last_p95_ms": 500.0}


def test_the_locustfile_names_no_host_user_or_password():
    text = perftest.LOCUSTFILE.read_text(encoding="utf-8")
    assert not re.search(r"https?://", text.split('"""', 2)[2])  # outside the docstring
    assert 'os.environ.get("LOADTEST_OFFICIAL_USERNAME"' in text and 'os.environ.get("LOADTEST_OFFICIAL_PASSWORD"' in text
    for name in ("feed", "post", "create", "notifications", "login", "refresh"):
        assert f'@tag("{name}")' in text

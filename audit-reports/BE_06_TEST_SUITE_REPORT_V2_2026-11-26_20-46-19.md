# Backend audit stage 6: test suite report, version 2

| | |
|---|---|
| Report | BE_06_TEST_SUITE_REPORT, **V2** (V1: `BE_06_TEST_SUITE_REPORT_V1_2026-11-23_22-28-30.md`) |
| Date and time | 2026-11-26 20:46:19 IST |
| Stage | S6, test suite (Jira BVTGC-17, part 3) |
| Repo, branch, commit | BVTGramaConnect_BackEnd, branch `test/BVTGC-17-part3`, from `dev` at `745a56e` |
| Auditor | Claude (AI-assisted audit), with the backend developer |
| Input reports | `BE_01_DISCOVERY_INVENTORY_V1_2026-11-09_05-34-09.md` (the endpoint inventory), `BE_02_TECH_AUDIT_V1_2026-11-09_11-25-02.md`, `BE_03_DATABASE_QUERY_AUDIT_V1_2026-11-09_11-44-26.md`, the three responses (`BE_01/02/03_RESPONSE_*`), `BE_06_TEST_SUITE_REPORT_V1_2026-11-23_22-28-30.md` |
| Scope covered | The stage 6 prompt (config-driven backend suite): unit, integration, RBAC matrix, object-level access, contract snapshot, security attacks, throttling and lockout, database (query budgets, rollback, migrations both ways), concurrency on a real database, background jobs, error handling, and the load, stress, spike and soak runner with its dashboard |
| Scope not covered | Load, stress, spike and soak **runs at real size**: the runner is built and was tried on a laptop server, but the runs wait for the staging server (BVTGC-38) and the expected users (BVTGC-37). Real Firebase, real Play Integrity and real ffmpeg (staging, BVTGC-32 and 33). The mobile app and the website (their own repos) |
| Application code changed | **None.** Only tests, test tooling (`grama/testsuite*.py`, `grama/testdashboard.py`, `grama/perftest.py`), test config, `pytest.ini`, `.gitignore`, `.env.test.example`, the locustfile and docs |

## In short

| | |
|---|---|
| Result | Laptop (SQLite): **5,060 passed, 0 failed, 8 known bugs (xfail), 14 skipped** (each skip explained below). CI on MariaDB 11.4 ran the real-thread tests and found BE-TEST-211 (below); with it marked, every database is green |
| Health score | **94.9 / 100, risk LOW** (dashboard; the score is an estimate until stage 7 confirms it) |
| Line coverage | **96.3%** of `apps/` and `grama/` (tests, migrations and the test tooling not counted) |
| API coverage | **100%**: all 167 API methods are called by at least one test |
| RBAC matrix | **80 of 167 API methods (47.9%)** checked for **every one of 8 roles** in one reviewable config file; the 87 others (ids in the path) are checked per object by the area tests (outside the area answers 404) |
| New in V2 | RBAC matrix config, token and session edge cases, API schema snapshot, attack inputs in every query parameter, hostile uploads, real-thread concurrency tests, migrations both ways, pytest markers, HTML dashboard with health score, the load-test runner and its profiles |
| Bugs found | **3**: **BE-TEST-211 (Medium)**, uploads sent at the same moment pass the photo limit of 3 (the BE-DB-014 guard recounts a prefetched list); BE-TEST-208 (Low), a number too large for the database in an area filter answers 500 on 7 lists (SQLite and PostgreSQL; MariaDB answers 400); BE-TEST-209 (Low), the contract promises HEIC photos, the server refuses them. All linked to xfail tests; not fixed in this stage, by the stage's rule |
| Time | 11 to 19 minutes for everything with coverage (the laptop varies), 7 minutes without |

## How to run it

```
python -m grama.testsuite all              # everything, report and dashboard
python -m grama.testsuite all --coverage   # the same with line coverage per app
python -m grama.testsuite security         # one category (--list shows them)
python -m pytest -q -p no:logging -m rbac  # by marker: unit, integration, rbac, contract, security, db,
                                           # concurrency, jobs, errors, or a category's key
python -m grama.perftest load --check      # the load runner: checks the config and the target only
python -m grama.perftest load|stress|spike|soak   # against staging only (.env.test)
```

The prompt's `python -m tests.runner` is `python -m grama.testsuite` here (the runner from V1), and load,
stress, spike and soak have their own runner, `python -m grama.perftest`, because they need a live server.

## Structure and files

### Added

| File | What it is |
|---|---|
| `grama/tests/config/rbac-matrix.json` | The reviewable RBAC matrix: 8 roles, 8 expectation profiles, 85 endpoint rows, each with the contract section it comes from |
| `grama/tests/config/openapi-snapshot.json` | The API's shape: 167 operations (the 2 DELETEs of #198, merged meanwhile, were reported as additive and added), request fields (type, required) and success-response fields (type), one level into lists and pages |
| `grama/tests/test_rbac_matrix.py` (85 tests) | Every endpoint row x every role, with real logins (JWT, X-Citizen-Token); the config checked for consistency; every parameter-free list of the API must be in the matrix |
| `grama/tests/test_token_edge_cases.py` (22) | No token, malformed tokens, a changed signature, a forged user id, an expired access token, a refresh token used as access and the other way round, a refresh token after logout, a blocked or deactivated official; console: no session, a write without CSRF, with CSRF, roles without the console permission |
| `grama/tests/test_schema_snapshot.py` (9) | Today's schema against the snapshot: a gone address or method, a gone response field, a changed type or a newly required request field fails; additions only print. The comparison itself is tested on small made-up schemas |
| `grama/tests/test_attack_inputs.py` (65) | 16 attack strings (SQL injection, template, path and script strings, Telugu with a quote) in every query parameter of all 29 lists in the schema; a search must not find more; tables intact afterwards; a number too large for the database; mass assignment on posts, users, a citizen phone and website messages |
| `apps/posts/tests/test_upload_attacks.py` (13) | Pictures claiming huge dimensions, polyglots (JPEG with HTML, ZIP or PHP appended), a PNG sent as JPEG, HTML, SVG with a script, GIF, a broken JPEG, an empty file, and the contract's photo formats |
| `apps/posts/tests/test_photo_limit_stale_count.py` (2) | BE-TEST-211 shown without threads on any database (xfail), and the same order of events with a fresh post refused |
| `grama/tests/test_concurrency_threads.py` (4) | Real threads on MariaDB or PostgreSQL: four reviewers publishing one post at once, eight posts from one phone against a limit of 3, one push token sent six times at once, six photos at once against the limit of 3. Skipped on SQLite with the reason |
| `grama/tests/test_migrations_both_ways.py` (4) | Each app's newest migration back and forward on an empty database; every data step has a reverse; on request (`RUN_MIGRATION_ROUND_TRIP=1`) every app to zero and back |
| `grama/tests/test_perftest.py` (17) | The load runner: production and qa refused, missing settings stop it, Locust's CSV becomes the figures, SLA per endpoint, stress breaking point, spike recovery, soak drift, no host or password in the locustfile |
| `grama/testdashboard.py` | The HTML dashboard and the HTML copy of each run's report |
| `grama/perftest.py` | The load, stress, spike and soak runner and its reports |
| `deploy/loadtest/profiles.json` | Profiles, SLA, refused hosts and the environment keys of the load runner |
| `.env.test.example` | The keys of the git-ignored `.env.test` |

### Changed

| File | Change |
|---|---|
| `grama/testsuite.py` | HTML copy of the report, the dashboard, start and end time, Python and Django versions, CPU and memory, a hash over all config files, known bugs (xfail) counted and listed |
| `grama/testsuite_plugin.py` | Loaded in every pytest run (`pytest.ini`): markers from the categories, `unit` for tests without the database, xfail recorded with its finding, the API routes the tests called |
| `grama/tests/suite.json` | `markers` (each category's stage markers), `dashboard` (score parts, weights, targets, risk limits), the new files in their categories |
| `grama/tests/test_suite_layout.py` (13) | Tests of the markers, xfail recording, API coverage, the health score and the dashboard file |
| `deploy/loadtest/locustfile.py` | Tags, and a staging official's mix (login, refresh, notifications, post list, a draft written and deleted) from environment values only |
| `pytest.ini` | Loads the plugin, which registers the markers |
| `.gitignore` | `.env.test` |
| `.claude/operations.md`, `.claude/conventions.md`, `.claude/backlog.md` | The commands and the rules: new lists go in the RBAC matrix, agreed API changes refresh the snapshot, bugs found by a test are marked xfail with their finding, load tests only through the runner |

### Config files and their keys

| File | Keys |
|---|---|
| `grama/tests/suite.json` | `categories` (title, about, files), `markers`, `dashboard.parts` (tests 70, line_coverage 10, api_coverage 10, rbac_coverage 10), `dashboard.weights` (per category), `line_coverage_target` 80, `api_coverage_target` 90, `risk` (LOW from 90, MEDIUM from 75; a failed test is never LOW) |
| `grama/tests/config/rbac-matrix.json` | `roles`, `answers` (allow, login 401, deny 403, hidden 404), `profiles` (public, any_login, officials, admins, super_admin, phone, wallet, wallet_off), `placeholders`, `endpoints` (method, path, profile, allow status, body, wallet switch, contract section) |
| `grama/tests/config/openapi-snapshot.json` | `"METHOD /path"` → `request`, `required`, `responses` |
| `deploy/loadtest/profiles.json` | `refused_hosts`, `required_env`, `optional_env`, `sla.default` and `sla.endpoints` (p95_ms, p99_ms, error_percent), `profiles.load` (users 1, 5, 10, 25, 50), `stress` (start, step, max, stop_when), `spike` (users, spawn_rate), `soak` (users, duration_s 600) |
| `.env.test` (git-ignored) | `LOADTEST_BASE_URL`, `LOADTEST_OFFICIAL_USERNAME`, `LOADTEST_OFFICIAL_PASSWORD`, `LOADTEST_METRICS_URL` |

Every report stores a hash of `suite.json`, `rbac-matrix.json` and `openapi-snapshot.json` (`cd47a421bd02…` on
the final run). Load reports store a hash of
`profiles.json` and the locustfile.

## The prompt's categories and where they are

| # | Category (marker) | Where | State |
|---|---|---|---|
| 1 | Unit (`unit`) | Every test that never touches the database is marked `unit` (349); coverage per app in the report | ✅ 96.3% overall, every app over 80% |
| 2 | Integration (`integration`) | The API tests of every app through `APIClient` | ✅ every API method called (100%) |
| 3 | RBAC matrix (`rbac`) | `rbac-matrix.json` + `test_rbac_matrix.py`; group x permission x area in `apps/accounts/tests/test_matrix.py`; token and session cases in `test_token_edge_cases.py` | ✅ |
| 4 | Object-level access (`rbac`) | `apps/posts/tests/test_access.py`, `apps/contacts/tests/test_access.py`, the area tests of each app, citizen token scope | ✅ (V1, unchanged) |
| 5 | Contract (`contract`) | `test_schema_snapshot.py`, `test_api_contract.py` (contract and server both ways), `test_client_calls.py` (every client call exists) | ✅ |
| 6 | Security (`security`) | `test_attack_inputs.py`, `test_upload_attacks.py`, EXIF/GPS in the media tests, no traceback with DEBUG off (`test_project.py`, `test_request_id.py`), `/api/docs/` and Django admin per settings (`test_django_admin_off.py`, `test_security_gaps.py`) | ✅ two findings |
| 7 | Throttling and lockout (`security`) | Lockout per username and address, per username, per address (`test_login_ip_cap.py`), X-Real-IP (`test_client_ip.py`, `test_real_ip_spoof.py`), the transliteration rate. The per-IP login throttle was removed by the lead's decision (2026-11-20), so there is no DRF login throttle to test | ✅ |
| 8 | Database (`db`) | Query budgets (10 files), rollback on a failed step (`test_audit_atomic.py`, `test_block_atomic.py`), signals and pushes sent only after commit (`django_capture_on_commit_callbacks`, 23 files), migrations both ways | ✅ |
| 9 | Concurrency (`concurrency`) | Step-by-step races (V1) and real threads on MariaDB and PostgreSQL in CI (`test_concurrency_threads.py`) | ✅ in CI; skipped on SQLite |
| 10 | Background jobs (`jobs`) | Notifications, retries, escalation, housekeeping, translation retries; synchronous in tests | ✅ (V1) |
| 11 | Error handling (`errors`) | `test_bad_input.py`, `test_errors_and_races.py`, `test_request_id.py`: database errors, timeouts of outside services, malformed JSON, wrong media type, all in the error envelope | ✅ (V1) |
| 12-15 | Load, stress, spike, soak (Locust) | `python -m grama.perftest`, `deploy/loadtest/profiles.json` | ⏳ runner built and tried; real runs on staging only (BVTGC-37, 38) |

## First run, per category

Final full run with coverage, 2026-11-26 21:08 IST (`test-reports/BE_TESTS_all_2026-11-26_21-08-11.md`, dashboard `backend-test-dashboard-2026-11-26_21-08-11.html`), Python 3.13.15, Django 5.2.18, SQLite, Windows, 8 CPUs, 15.9 GB.

| Category | Tests | Passed | Failed | Known bugs | Skipped |
|---|---:|---:|---:|---:|---:|
| Permissions and areas (`rbac`) | 2,324 | 2,317 | 0 | 0 | 7 |
| Login, sessions and passwords | 327 | 327 | 0 | 0 | 0 |
| Posts, review, feed and sharing | 389 | 388 | 0 | 0 | 1 |
| Citizens and service requests | 688 | 688 | 0 | 0 | 0 |
| Privacy and audit log | 78 | 78 | 0 | 0 | 0 |
| Notifications and background jobs | 111 | 111 | 0 | 0 | 0 |
| Areas, numbers, ads, bills and data commands | 400 | 399 | 0 | 0 | 1 |
| Query counts | 102 | 102 | 0 | 0 | 0 |
| Races, database differences and data migrations | 65 | 60 | 0 | 0 | 5 |
| API shape, errors and the contract | 431 | 431 | 0 | 0 | 0 |
| Security settings and server safety | 167 | 159 | 0 | 8 | 0 |
| **Total** | **5,082** | **5,060** | **0** | **8** | **14** |

After this run, CI's MariaDB job found BE-TEST-211; its two tests were added then (1 passed, 1 xfail), so there
are 9 known bugs on SQLite. The branch was also brought up to date with `dev` (#198 added its own tests), so
later runs count more tests than this table.

The 14 skips: 7 by design (one parametrised test applies only to some officials), 1 needs ffmpeg (staging),
1 calls Google live (`LIVE_TRANSLITERATION=1`), 4 real-thread tests need MariaDB or PostgreSQL (CI runs them),
1 is the full migration round trip, run on request: it **passed** on 2026-11-26 (1 h 44 min on the laptop).

**Load runner, first run.** `python -m grama.perftest load --smoke` against a laptop server with sample data
(not staging, to prove the runner): 1 and 2 users, 20 s each, 0% errors, P95 20 and 32 ms, SLA met, reports
`load-report-2026-11-26_17-53-02.json` and `.html`. These are not load figures; the real runs need staging.

## Coverage

| | |
|---|---|
| Line coverage | 96.3% (stage target 80% on core apps: every app is above it) |
| API coverage | 167 of 167 API methods (`HEAD` and `OPTIONS`, which DRF adds by itself, not counted) |
| RBAC matrix | 80 of 167 methods x 8 roles (anonymous, citizen phone, citizen wallet account, Panchayat member, Village, Mandal, District and Super Admin): every list and every create without an id in the path. The 87 methods with an id are checked per object by the area tests |
| Roles in the group x permission matrix | Every group x every permission x the same area, the parent, a sibling and another district (`test_matrix.py`, V1) |

## Findings

| ID | Severity | Finding | Test | Status |
|---|---|---|---|---|
| BE-TEST-211 | Medium | **Uploads sent at the same moment pass the photo limit.** On MariaDB, six photos uploaded to one post at once were all stored (limit 3). Cause: `apps/posts/media.py` `add_photo()` recounts inside the locked transaction with `post.media.all().count()`, and the post views load the post with `prefetch_related("media")` (`apps/posts/views.py`, `post_queryset`), so the recount reads the list loaded with the post, not the database. The row lock waits as meant, but the count after it is stale. Any database; one upload at a time is not affected (each request loads the post fresh). The same count guards "photos or one video, never both" (`add_video`). Reached by a double tap or a retry on a slow network. The fix BE-DB-014 (BVTGC-109) is therefore not effective through the API; its own test calls the service with a fresh post | `apps/posts/tests/test_photo_limit_stale_count.py` (xfail, strict, any database, no threads); `grama/tests/test_concurrency_threads.py::test_photos_uploaded_at_once_never_pass_the_limit` (xfail, MariaDB and PostgreSQL) | Open: count from the database in `_check_room` (`PostMedia.objects.filter(post=post)`), not the post's related manager |
| BE-TEST-208 | Low | A number too large for the database (`?district=99999999999999999999999999`) in the area filter of 7 lists answers **500** instead of 400: `admin/citizens/blocked/`, `geo/admin/villages/`, `geo/districts/`, `geo/mandals/`, `geo/villages/`, `posts/`, `users/`. The filter hands the number to the database, which cannot hold it (SQLite OverflowError; PostgreSQL "integer out of range"). Nothing leaks, and the error envelope and request id are kept; it only shows up as a server error in the logs | `grama/tests/test_attack_inputs.py::test_a_number_too_large_for_the_database_in_every_query_parameter` (xfail, strict on SQLite) | Open: fix by checking the filter is a valid id (400 `validation_error`) |
| BE-TEST-209 | Low | `docs/api-contract.md`, section 5, says photos may be "JPEG, PNG, WebP or HEIC"; `apps/posts/media.py` takes JPEG, PNG and WebP and no HEIC decoder is installed, so a HEIC photo answers 415. Whether a client ever sends HEIC (iPhones convert to JPEG in most pickers) is for the mobile team to confirm | `apps/posts/tests/test_upload_attacks.py::test_the_photo_formats_in_the_contract_are_the_ones_the_server_takes` (xfail, strict) | Open: either take HEIC (a new package, needs approval) or remove HEIC from the contract |
| BE-TEST-210 | Info | The stage prompt's stack says "settings files base, dev, prod, test". The project has one `grama/settings.py`; the old module names are aliases of it (`.claude/architecture.md`). No change needed | – | Noted |

No other bug was found: the SQL-injection, template and path strings in all 29 lists, mass assignment, hostile
uploads, token and session cases and the 80 x 8 RBAC cells all behave as the contract says.

**On MariaDB 11.4 (CI, 2026-11-26):** the other three real-thread tests pass: of four reviewers publishing
one post at once exactly one wins and three get `409 already_decided`; eight posts from one phone at once
against a limit of 3 store exactly 3; one push token sent six times at once is stored once. BE-TEST-208 does
not occur there (MariaDB compares the large number and finds nothing: 400 or an empty list), so its mark is
strict on SQLite only.

### Closed since V1

| ID | Was | Now |
|---|---|---|
| BE-TEST-203 | Every test ran on SQLite only | Closed (PR #181): CI runs the whole suite on MariaDB 11.4 and PostgreSQL 17, now also the real-thread tests |
| BE-TEST-204 | The lockout bypass test was removed | Closed (BVTGC-105, PR #175) |
| BE-TEST-205 | No load test | Partly: runner, profiles, SLA and reports built and tried; the runs wait for staging (BVTGC-37, 38) |
| BE-TEST-206 | Real ffmpeg, Firebase, live Google not exercised | Open, staging |
| BE-TEST-207 | The `translations` command had no test | Closed (BVTGC-112) |

## Differences from the prompt, on purpose

| Prompt | Here | Why |
|---|---|---|
| `tests/config/`, `loadtests/`, `python -m tests.runner` | `grama/tests/config/`, `deploy/loadtest/`, `python -m grama.testsuite` and `python -m grama.perftest` | The repo allows no new top-level folder without the team's agreement (`.claude/conventions.md`); the runner and the locustfile already lived there |
| YAML config | JSON | The prompt allows JSON; no new package |
| Reports `<category>-report-<ts>` | `BE_TESTS_<scope>_<ts>.md/.json/.html` for test runs, `<kind>-report-<ts>.json/.html` for load runs | The V1 name is kept so earlier reports and the docs stay valid |
| Test users and passwords from config | Fixed fake values in `conftest.py` for the test database; real ones only for load runs, from the git-ignored `.env.test` | The tests build their own database; nothing real is used |
| DRF throttle limits hit and reset | Lockouts and the transliteration rate | The per-IP login throttle was removed by the lead's decision of 2026-11-20 |

## CI integration

- Nothing to add for the tests: CI runs `python -m pytest -q -p no:logging`, which now loads the plugin (markers)
  through `pytest.ini`, so every new test runs on SQLite, MariaDB and PostgreSQL in every pull request into `dev`.
- The real-thread tests run in the MariaDB and PostgreSQL jobs (they skip on SQLite).
- Optional, for the team to decide: a job that runs `python -m grama.testsuite all --coverage` and keeps
  `test-reports/` as an artifact, so the dashboard is downloadable from every pull request.
- Load runs are never in CI: they need staging and are started by hand.

## Confirmation

No application code changed: no file under `apps/` outside `tests/`, no settings, migration, URL, view,
serializer or deployment script. The API, its contract and every permission are as before.

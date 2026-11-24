# Backend audit stage 6: test suite report

| | |
|---|---|
| Repo | BVTGramaConnect_BackEnd (Django REST API) |
| Jira | BVTGC-17, "[Backend] Audit S6: Test suite" |
| Written | 2026-11-23 22:28 IST (part 1); updated 2026-11-24 10:55 IST (part 2: line coverage) |
| Written by | The backend developer (with Claude, AI-assisted) |
| Parts | 1: categories, runner, gap tests (no new packages). 2: line coverage (one new package, `coverage`, approved 2026-11-24). No application code changed |
| Run on | branch `test/BVTGC-17-test-suite`, on `dev` at `14cfe64`, Python 3.13.15, SQLite |

## In short

| | |
|---|---|
| Result | **PASS: 3,490 of 3,499 tests pass, 0 fail, 9 skipped** (each skip explained below) |
| Line coverage | **96.0%**: 10,692 of 11,138 statements in `apps/` and `grama/` (tests and migrations not counted) |
| Time | 6.9 minutes for the whole suite; 12.8 minutes with coverage |
| Test files | 127, each in exactly one of 11 categories |
| New in this stage | the category runner with an optional coverage report, 3 new test files (7 tests), one contract fix |
| Bugs found | None in the code. One wording slip in `docs/api-contract.md`, fixed |
| Risk | **LOW** for what the suite covers. Tests on the server database and a load test are still missing (open items below) |

The backend already had a large suite before this stage: 3,486 tests on `dev`, written with each feature and each
audit fix, many of them worked out from the written rules rather than from the code. Stage 6 makes it readable and
repeatable for the audit: every test file has a category, one command runs it and writes a dated report with line
coverage, and a normal test run fails if a new file is left without a category.

## How to run it

```
python -m grama.testsuite all              # everything, one pytest run, about 7 minutes
python -m grama.testsuite all --coverage   # the same with line coverage per app, about 13 minutes
python -m grama.testsuite permissions      # one category
python -m grama.testsuite --list           # the categories
python -m grama.testsuite --check          # only check every test file has one category
```

On Windows use `.venv\Scripts\python`, on Linux or macOS `.venv/bin/python`. Each run writes
`test-reports/BE_TESTS_<scope>_<date>_<time>.md` and `.json` (git-ignored): result, commit and branch (marked when
there are uncommitted changes), the sha256 of the categories file, counts per category, line coverage when asked,
every failure, every skip with its reason, and the ten slowest tests. The exit code is pytest's, so 0 means
everything passed. The usual `python -m pytest -q -p no:logging` still runs the same tests.

## What was added

| File | What it does |
|---|---|
| `grama/tests/suite.json` | The 11 categories and the test files in each |
| `grama/testsuite.py` | The runner and its report; `--coverage` runs the tests under coverage.py. The tests run in a pytest child process with the normal settings |
| `grama/tests/test_suite_layout.py` (4 tests) | A test file in no category, in two, or listed but gone fails a normal pytest run, so CI catches it too; and the coverage summary adds up per app |
| `grama/tests/test_log_leak.py` (1 test) | A full session leaves no password, token, full mobile number or full email in the server log (below) |
| `grama/tests/test_api_contract.py` (2 tests) | The contract and the server's addresses match, both ways (below) |
| `requirements.txt` | `coverage==7.17.5` (part 2) |
| `.gitignore`, `.claude/operations.md`, `.claude/conventions.md` | `test-reports/` ignored; the command and the "one category per file" rule written down |
| `docs/api-contract.md` | One wording fix found by the new contract test |

The category check proved itself during this stage: PR #137 merged a new test file
(`apps/accounts/tests/test_users_contact_masking.py`) while this work was open, and the runner refused to run until
it was given a category (privacy).

### The log-leak test
One test walks every path that handles secrets or personal data:
- app login with a wrong password, then the right one; token refresh and logout;
- console login with a session;
- a password typed into the username box (audit BE-SEC-007);
- an admin creating an official with a typed temporary password, mobile and email, and resetting another official's
  password (a generated one, shown once);
- an official changing their own password;
- a citizen phone registering, posting, and asking for a service with name, mobile, email and address.

Every log record of every logger is caught, at every level, with its arguments, extra fields and any traceback.
None of the 16 secrets and personal values may appear in any of them. It passes. To prove it can fail, a copy that
logged the password once was run and failed as expected (the copy was then deleted).

### The contract test
- Every one of the server's **104** addresses under `/api/v1/` is written in `docs/api-contract.md`.
- Every address the contract writes as `METHOD /path` exists on the server, under `/api/v1/` or at the domain root
  (`/p/<code>/`, `/app/`, `/.well-known/assetlinks.json`, `/api/schema/`). This is audit finding BE-API-002, now
  checked on every run instead of once.
- **Found:** the contract's review section said `POST /review` for `POST /posts/{id}/review`. Fixed; no API change.

## Results by category

Full run with coverage of 2026-11-24 10:42 IST (`test-reports/BE_TESTS_all_2026-11-24_10-42-46.md`).

| Category | What it covers | Files | Tests | Passed | Skipped |
|---|---|---:|---:|---:|---:|
| Permissions and areas | every group × permission × area, ranks, privilege escalation, who sees which post | 12 | 1,759 | 1,752 | 7 |
| Login, sessions and passwords | app JWT, console session, token version, lockout, password rules, usernames | 5 | 153 | 153 | 0 |
| Posts, review, feed and sharing | status changes, first decision wins, reverse, media, translation, feed, share links | 17 | 324 | 323 | 1 |
| Citizens and service requests | registration, posting token, daily limits, blocks, citizen review, requests, cancel | 14 | 271 | 271 | 0 |
| Privacy and audit log | masking, no secrets in the audit log or the server log, audit saved with its change | 10 | 59 | 59 | 0 |
| Notifications and background jobs | push and inbox, retries, escalation, housekeeping, the worker | 14 | 102 | 102 | 0 |
| Areas, numbers, ads, bills and data commands | geography writes and imports, important numbers, banners, bills, Telugu names, seed commands | 17 | 391 | 390 | 1 |
| Query counts | a fixed number of queries per page or job, whatever the rows (N+1 guards) | 10 | 85 | 85 | 0 |
| Races, database differences and data migrations | requests arriving together, SQLite vs MySQL and PostgreSQL, data steps | 8 | 37 | 37 | 0 |
| API shape, errors and the contract | error shape, 400/404 not 500, contract and schema, languages, request id, the suite's categories | 16 | 267 | 267 | 0 |
| Security settings and server safety | settings fail safe, Django admin off on servers, security gap fixes | 4 | 51 | 51 | 0 |
| **Total** | | **127** | **3,499** | **3,490** | **9** |

### The 9 skips
| How many | Why | Is that a gap? |
|---:|---|---|
| 7 | One parametrised test in `posts/test_area_posts_visible.py` applies only to the officials a newer rule covers; the other cases skip | No, by design |
| 1 | `posts/test_video_metadata.py`: the real ffmpeg run needs ffmpeg and ffprobe, which this laptop does not have (fakes cover the rest) | Run it on the staging server |
| 1 | `web_admin/test_transliterate.py`: the live call to Google Input Tools runs only with `LIVE_TRANSLITERATION=1` | Run by hand before a release |

## Line coverage (part 2)

| Part | Statements | Covered | % |
|---|---:|---:|---:|
| `apps/accounts` | 2,314 | 2,207 | 95.4 |
| `apps/ads` | 299 | 297 | 99.3 |
| `apps/audit` | 339 | 332 | 97.9 |
| `apps/bills` | 214 | 208 | 97.2 |
| `apps/citizens` | 809 | 793 | 98.0 |
| `apps/contacts` | 907 | 886 | 97.7 |
| `apps/feed` | 253 | 250 | 98.8 |
| `apps/geography` | 1,337 | 1,296 | 96.9 |
| `apps/notifications` | 672 | 636 | 94.6 |
| `apps/posts` | 1,755 | 1,706 | 97.2 |
| `apps/service_requests` | 695 | 687 | 98.8 |
| `apps/share` | 143 | 137 | 95.8 |
| `apps/web_admin` | 356 | 321 | 90.2 |
| `apps/web_public` | 232 | 225 | 97.0 |
| `grama` | 813 | 711 | 87.5 |
| **Total** | **11,138** | **10,692** | **96.0** |

Least covered files, and why:

| File | Statements | % | Why |
|---|---:|---:|---|
| `apps/web_admin/management/commands/translations.py` | 20 | 0.0 | The command is a thin wrapper; `grama/tests/test_i18n.py` tests the catalogue code it calls, not the command itself |
| `grama/i18n.py` | 145 | 71.0 | Some branches of the catalogue writer are not reached by `test_i18n.py`; not yet looked at line by line |
| `grama/settings.py` | 258 | 83.7 | The production branches are tested in fresh Python processes (`test_settings_safety.py`), which coverage does not follow |
| `apps/geography/admin.py` | 39 | 84.6 | Django admin, laptops only |
| `apps/notifications/push.py` | 87 | 85.1 | The real Firebase gateway: needs Firebase credentials (BVTGC-32) |
| `apps/accounts/management/commands/create_super_admin.py` | 45 | 86.7 | Some branches not reached; not yet looked at line by line |
| `apps/web_admin/transliteration.py` | 47 | 87.2 | The live Google call (skipped test above) |
| `apps/share/og.py` | 42 | 88.1 | Some branches not reached; not yet looked at line by line |
| `apps/posts/management/commands/translate_published_posts.py` | 27 | 88.9 | Some branches not reached; not yet looked at line by line |

None of these is a permission, privacy or post-status rule; those parts are at 95% to 99%.

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| BE-TEST-201 | Info | The contract said `POST /review` for `POST /posts/{id}/review` | ✅ Fixed in this stage |
| BE-TEST-202 | Medium | No coverage figure: nothing measured which lines the tests run | ✅ Fixed in part 2: 96.0% |
| BE-TEST-203 | Medium | Every test runs on SQLite only; nothing runs on MySQL or PostgreSQL (BE-DB-021) | ⏳ After the server database is chosen (BVTGC-38): one CI job on it |
| BE-TEST-204 | Medium | The lockout bypass test (`test_real_ip_spoof.py`) was removed with the BE-SEC-101 revert on 2026-11-20 | ⏳ Comes back with the fix, before going live (backlog item 9) |
| BE-TEST-205 | Low | No load test at the real size; query-count tests guard N+1s but not throughput (BE-TEST-002) | ⏳ After the expected number of users (BVTGC-37) |
| BE-TEST-206 | Low | Real ffmpeg, real Firebase and live Google are not exercised on laptops | ⏳ On the staging server |
| BE-TEST-207 | Low | The `translations` command itself has no test (0%) | ⏳ A small test can call it; optional |

No bugs were found in the application code.

## What is not covered, by design

- Real Firebase delivery, real Play Integrity verdicts, real ffmpeg on a server, Nginx and HTTPS cookies: these need
  the staging server and the client's accounts (backlog items 2 to 7; BVTGC-32, 33).
- The mobile app and the website are tested in their own repos (BVTGC-12, BVTGC-7).

## Still to do after this stage

1. **Server database.** Once BVTGC-38 is decided: a GitHub job that runs the suite on MySQL or PostgreSQL.
2. **Before going live:** the lockout bypass fix and its test come back together (BE-TEST-204).

# Backend audit stage 6: test suite report

| | |
|---|---|
| Repo | BVTGramaConnect_BackEnd (Django REST API) |
| Jira | BVTGC-17, "[Backend] Audit S6: Test suite" |
| Written | 2026-11-23 22:28 IST |
| Written by | The backend developer (with Claude, AI-assisted) |
| Part | 1 of 2: no new packages, no application code changed |
| Run on | branch `test/BVTGC-17-test-suite`, from `dev` at `55f6765`, Python 3.13.15, SQLite |

## In short

| | |
|---|---|
| Result | **PASS: 3,483 of 3,492 tests pass, 0 fail, 9 skipped** (each skip explained below) |
| Time | 6.9 minutes for the whole suite |
| Test files | 126, each in exactly one of 11 categories |
| New in this stage | the category runner, 3 new test files (6 tests), one contract fix |
| Bugs found | None in the code. One wording slip in `docs/api-contract.md`, fixed |
| Risk | **LOW** for what the suite covers. Coverage figures, tests on the server database and a load test are still missing (part 2 and the open items below) |

The backend already had a large suite before this stage: 3,486 tests on `dev`, written with each feature and each audit fix,
many of them worked out from the written rules rather than from the code. Stage 6 makes it readable and repeatable
for the audit: every test file has a category, one command runs it and writes a dated report, and a normal test run
fails if a new file is left without a category.

## How to run it

```
python -m grama.testsuite all            # everything, one pytest run, about 7 minutes
python -m grama.testsuite permissions    # one category
python -m grama.testsuite --list         # the categories
python -m grama.testsuite --check        # only check every test file has one category
```

On Windows use `.venv\Scripts\python`, on Linux or macOS `.venv/bin/python`. Each run writes
`test-reports/BE_TESTS_<scope>_<date>_<time>.md` and `.json` (git-ignored): result, commit and branch (marked when
there are uncommitted changes), the sha256 of the categories file, counts per category, every failure, every skip
with its reason, and the ten slowest tests. The exit code is pytest's, so 0 means everything passed.
The usual `python -m pytest -q -p no:logging` still runs the same tests.

## What was added

| File | What it does |
|---|---|
| `grama/tests/suite.json` | The 11 categories and the test files in each |
| `grama/testsuite.py` | The runner and its report. Standard library only; the tests run in a pytest child process with the normal settings |
| `grama/tests/test_suite_layout.py` (3 tests) | A test file in no category, in two, or listed but gone fails a normal pytest run, so CI catches it too |
| `grama/tests/test_log_leak.py` (1 test) | A full session leaves no password, token, full mobile number or full email in the server log (below) |
| `grama/tests/test_api_contract.py` (2 tests) | The contract and the server's addresses match, both ways (below) |
| `.gitignore`, `.claude/operations.md`, `.claude/conventions.md` | `test-reports/` ignored; the command and the "one category per file" rule written down |
| `docs/api-contract.md` | One wording fix found by the new contract test |

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

Full run of 2026-11-23 22:28 IST (`test-reports/BE_TESTS_all_2026-11-23_22-28-12.md`).

| Category | What it covers | Files | Tests | Passed | Skipped |
|---|---|---:|---:|---:|---:|
| Permissions and areas | every group × permission × area, ranks, privilege escalation, who sees which post | 12 | 1,759 | 1,752 | 7 |
| Login, sessions and passwords | app JWT, console session, token version, lockout, password rules, usernames | 5 | 153 | 153 | 0 |
| Posts, review, feed and sharing | status changes, first decision wins, reverse, media, translation, feed, share links | 17 | 324 | 323 | 1 |
| Citizens and service requests | registration, posting token, daily limits, blocks, citizen review, requests, cancel | 14 | 271 | 271 | 0 |
| Privacy and audit log | masking, no secrets in the audit log or the server log, audit saved with its change | 9 | 53 | 53 | 0 |
| Notifications and background jobs | push and inbox, retries, escalation, housekeeping, the worker | 14 | 102 | 102 | 0 |
| Areas, numbers, ads, bills and data commands | geography writes and imports, important numbers, banners, bills, Telugu names, seed commands | 17 | 391 | 390 | 1 |
| Query counts | a fixed number of queries per page or job, whatever the rows (N+1 guards) | 10 | 85 | 85 | 0 |
| Races, database differences and data migrations | requests arriving together, SQLite vs MySQL and PostgreSQL, data steps | 8 | 37 | 37 | 0 |
| API shape, errors and the contract | error shape, 400/404 not 500, contract and schema, languages, request id | 16 | 266 | 266 | 0 |
| Security settings and server safety | settings fail safe, Django admin off on servers, security gap fixes | 4 | 51 | 51 | 0 |
| **Total** | | **126** | **3,492** | **3,483** | **9** |

### The 9 skips
| How many | Why | Is that a gap? |
|---:|---|---|
| 7 | One parametrised test in `posts/test_area_posts_visible.py` applies only to the officials a newer rule covers; the other cases skip | No, by design |
| 1 | `posts/test_video_metadata.py`: the real ffmpeg run needs ffmpeg and ffprobe, which this laptop does not have (fakes cover the rest) | Run it on the staging server |
| 1 | `web_admin/test_transliterate.py`: the live call to Google Input Tools runs only with `LIVE_TRANSLITERATION=1` | Run by hand before a release |

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| BE-TEST-201 | Info | The contract said `POST /review` for `POST /posts/{id}/review` | ✅ Fixed in this stage |
| BE-TEST-202 | Medium | No coverage figure: nothing measures which lines the tests run | ⏳ Part 2: needs one new pinned package (`coverage`), owner's or lead's approval |
| BE-TEST-203 | Medium | Every test runs on SQLite only; nothing runs on MySQL or PostgreSQL (BE-DB-021) | ⏳ After the server database is chosen (BVTGC-38): one CI job on it |
| BE-TEST-204 | Medium | The lockout bypass test (`test_real_ip_spoof.py`) was removed with the BE-SEC-101 revert on 2026-11-20 | ⏳ Comes back with the fix, before going live (backlog item 9) |
| BE-TEST-205 | Low | No load test at the real size; query-count tests guard N+1s but not throughput (BE-TEST-002) | ⏳ After the expected number of users (BVTGC-37) |
| BE-TEST-206 | Low | Real ffmpeg and live Google are not exercised on laptops (2 skips) | ⏳ On the staging server |

No bugs were found in the application code.

## What is not covered, by design

- Real Firebase delivery, real Play Integrity verdicts, real ffmpeg on a server, Nginx and HTTPS cookies: these need
  the staging server and the client's accounts (backlog items 2 to 7; BVTGC-32, 33).
- The mobile app and the website are tested in their own repos (BVTGC-12, BVTGC-7).

## Part 2: needs approval

1. **Coverage.** Add `coverage` (pinned) to `requirements.txt` and let `python -m grama.testsuite all --coverage`
   add the line coverage per app to the report. One new development package, nothing on the server changes.
2. **Server database.** Once BVTGC-38 is decided: a GitHub job that runs the suite on MySQL or PostgreSQL.

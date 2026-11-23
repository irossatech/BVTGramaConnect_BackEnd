# BACKEND DATABASE AND QUERY AUDIT

| Item | Value |
|---|---|
| Report | BE_03 Database and Query Audit |
| Version | V1 (first BE_03 in `audit-reports/`) |
| Date and time (IST) | 2026-11-09 11:44:26 |
| Repository | `C:\BVTGramaConnect_BackEnd` |
| Branch audited | `docs/single-settings-file` |
| Commit audited | `db64d8417bbf4b47b7f0e9d72c9cf33f9ce50735` |
| Auditor | Claude (AI-assisted audit) |
| Stage | 3: deep dive into database connections, queries, transactions, migrations and background jobs |
| Mode | Read-only. This report is the only file written in the repo. |

**Scope covered**
- Settings for database, cache, sessions and django-q2 (`grama/settings.py`).
- Deploy templates and scripts that decide connection counts and schedules (`deploy/`).
- Every model, index and constraint in the 14 apps.
- The query paths of the list and detail endpoints, the console reports and dashboard, the jobs, and the Django admin `__str__`/`list_select_related` where relevant.
- Transactions and `on_commit` use, all 56 migrations, and django-q2's ORM broker and scheduler as installed in `.venv`.

**Scope not covered**
- Live MySQL or PostgreSQL servers: no `EXPLAIN`, slow-query log, `max_connections` or collation could be read.
- The production database on the hosting panel. Its setup is outside the repo (BE-SEC-003).
- Load tests (`deploy/loadtest/`) were not run.
- The web and mobile repos were not needed for any finding.

**Earlier reports used as input**
- `audit-reports/BE_01_DISCOVERY_INVENTORY_V1_2026-11-09_05-34-09.md`
- `audit-reports/BE_02_TECH_AUDIT_V1_2026-11-09_11-25-02.md`

Where a finding continues one from those reports, the old ID is named, for example "(carries BE-PERF-002)".

**Measurements taken (all read-only for the repo)**

| What | Command | Result |
|---|---|---|
| Migration drift | `.venv/Scripts/python manage.py makemigrations --check --dry-run` | `No changes detected`, exit 0. The local `db.sqlite3` was not modified; its timestamp stayed 2026-11-04 10:15. |
| Test suite | `.venv/Scripts/python -m pytest -q -p no:logging -p no:cacheprovider --durations=10` | **2867 passed, 2 skipped in 177.73 s.** The skips need ffmpeg and live Google. |
| Query counts as rows grow | A throwaway pytest file in the session scratchpad, outside the repo, run on pytest-django's temporary SQLite database with the repo's own `conftest.py` helpers | See the table below |

| Endpoint or function (fixture map: 2 districts, 4 mandals, 8 villages) | Small | Larger | Growth |
|---|---|---|---|
| `GET review/queue/?source=citizen` (District Admin) | 1 citizen post: 17 queries | 10 posts from 10 phones: 44 | **+3 per post** |
| `GET posts/` (District Admin, citizen posts) | 1 post: 16 | 10 posts: 43 | **+3 per post** |
| `GET service-requests/` (District Admin) | 1 request: 12 | 8 requests: 30 | **about +2.6 per row** |
| `GET admin/dashboard/` | 31 queries | n/a | grows per district (code) |
| `GET admin/reports/posts-by-area/` | 18 queries | n/a | one or more per district and per mandal (code) |
| `GET admin/reports/admin-activity/` | 20 queries | n/a | n/a |
| `GET admin/reports/summary/` | 8 queries | n/a | flat |
| `notifications.services.notify_users()` | 1 recipient: 9 | 6 recipients: 54 | **+9 per recipient** (tests run django-q in `sync` mode, so this includes running the push job; in production about 4 to 5 per recipient stay in the request) |

The measurements count queries only. No latency, row counts or sizes were measured on a real database.

---

## 1. Executive summary

| | |
|---|---|
| **Database health score** | **6.5 / 10** |
| **Backend score impact** | BE_01 rated "Data model and database" 8/10. Under production-scale query paths this stage puts database and performance at 6.5/10: the schema is sound, but several read paths and the data lifecycle are not ready for state-wide volume. |
| **Production readiness** | **Ready for a pilot** (a few districts, low post volume). **Not ready for state-wide scale** until the High items and most Medium items in section 15 are done. |
| **Risk category** | **Medium.** No data-loss or corruption risk was found in the write path. The risks are slow console pages and timeouts, unbounded table growth, and SQLite/production differences that no test covers. |

**What is strong.**
- Post status changes are single conditional UPDATEs, so the first decision wins on every engine with no locks.
- Audit and review records are written in the same transaction, and signals fire through `transaction.on_commit`.
- Authorization lookups are cached and batched.
- The citizen feed and the inbox use cursor pagination with query-count tests.
- Indexes match the feed, the queue and the audit filters.
- The schema has no migration drift, and the whole suite passes.

**Top risks**
1. **BE-DB-001 (High).** The console reports and dashboard run one or more queries per district and per mandal, aggregate in Python and page in Python. `posts-by-area` grows with the number of mandals, and its CSV export runs the same work against nginx's 60-second limit.
2. **BE-DB-002 (High).** The audit log never shrinks by design, yet every console audit page runs an OFFSET page, a `COUNT(*)` and a second `GROUP BY` count. Text search uses four `icontains` filters. The export loads up to 50,000 full rows, JSON included, into memory.
3. **BE-DB-003, BE-DB-005, BE-DB-006 (Medium).** N+1 query patterns: notification fan-out (+9 queries per recipient measured), citizen summaries in the post list and review queue (+3 per citizen post), and service-request `allowed_actions` (about +2.6 per row).
4. **BE-DB-018 (Medium).** No retention for notifications, citizen devices, officials' and citizens' FCM `Device` rows, contact messages, review actions or service-request events.
5. **BE-DB-004 and BE-DB-014 (Medium).** Check-then-act rules with no database guard: two Super Admins blocking each other at once can leave zero active Super Admins, and daily limits and the photo cap can be exceeded under concurrency.

**Findings by severity:** Critical 0 · High 2 · Medium 12 · Low 9 · Info 4 (27 in total, BE-DB-001 to BE-DB-027).

**Ratings by area**

| Area | Rating | Justification |
|---|---|---|
| Connections | 7/10 | Persistent connections with health checks and a small connection budget. MySQL `sql_mode` is narrowed and the production server settings are not in the repo. |
| Queries and N+1 | 6/10 | Feed, inbox and the main lists are batched and tested. Reports, notifications, citizen summaries and service requests grow per row. |
| Indexes | 7/10 | Composite indexes exist for the feed, queue, audit, inbox, targets and citizens. `Post.created_at` and `ServiceRequest.updated_at` (both list orderings) are not indexed, and one Post index is now unused. |
| Pagination and counts | 6/10 | Cursors where it matters most. The audit log, posts, users and service requests use OFFSET plus one to four extra COUNTs per page. |
| Write path and concurrency | 7/10 | Conditional updates and unique constraints back the main rules. Several limits are check-then-insert. |
| Transactions | 8/10 | Multi-step writes are atomic and side effects go after commit. Files are written inside transactions, and a few multi-row actions are not atomic as a whole. |
| Migrations | 7/10 | No drift, and data migrations are mostly small. Several are irreversible, MySQL DDL is not transactional, and deploys take no backup before migrating. |
| Background jobs | 6/10 | The broker is safe against duplicates and housekeeping exists. The poll is unset (0.2 s default), retries sleep inside workers, and escalation runs unbatched under a 120 s timeout. |
| Data growth and retention | 4/10 | Only sessions, JWTs, axes logs and failed tasks are cleaned. Most user-driven tables grow forever. |
| SQLite vs production parity | 5/10 | Collation, NULL ordering, locking and JSON behaviour differ, and no test runs on MySQL or PostgreSQL. |

---

## 2. Connection audit

### 2.1 `DATABASES` as configured

There is now **one settings file**, `grama/settings.py`; `grama.settings.base/.dev/.prod/.test` are aliases of it (`settings.py:553-557`). The brief's "settings files base, dev, prod, test" no longer matches the code (BE-DB-025).

| Setting | Development (`DEBUG` on or unset) | Production and qa (`DEBUG=False`) | Tests (pytest loaded, `DEBUG` on) | Evidence |
|---|---|---|---|---|
| Engine | SQLite, or `DATABASE_URL` if set | `DATABASE_URL` (required): MySQL or PostgreSQL | SQLite. With `DEBUG=False`, tests would use `DATABASE_URL` (BE-TEST-001, carried) | `settings.py:89-93` |
| SQLite options | `timeout` 20, `transaction_mode` IMMEDIATE, WAL, `synchronous=NORMAL` | n/a | same | `settings.py:79-87` |
| `CONN_MAX_AGE` | 60 (env `DB_CONN_MAX_AGE`), URL databases only; SQLite is not given one (Django default 0) | 60 | 0 | `settings.py:74` |
| `CONN_HEALTH_CHECKS` | True (URL databases) | True | n/a | `settings.py:75` |
| `ATOMIC_REQUESTS` | not set, so False | not set, so False | not set | `settings.py` (absent) |
| MySQL charset | `utf8mb4` | `utf8mb4` | `TEST` CHARSET utf8mb4, COLLATION `utf8mb4_unicode_ci` | `settings.py:69, 71-73` |
| MySQL `sql_mode` | `init_command = "SET sql_mode='STRICT_TRANS_TABLES'"` | same | n/a | `settings.py:70` |
| MySQL isolation level | not set, so Django's default **READ COMMITTED** | same | n/a | `django/db/backends/mysql/base.py:238` (installed Django) |
| MySQL server collation | `utf8mb4_unicode_ci` on the `setup.sh` path only | Production panel database: **not in the repo** | n/a | `deploy/setup.sh:206-216, 222` |
| PostgreSQL options | none; database created `UTF8`, `--locale=C.UTF-8` | same | n/a | `deploy/setup.sh:251` |

**Assessment.**
- Persistent connections with health checks suit sync Gunicorn workers.
- READ COMMITTED on MySQL fits the conditional-UPDATE design.
- No `ATOMIC_REQUESTS` is right here: services open their own transactions, and a request-wide transaction would hold locks during uploads and outside calls.

### 2.2 Connection budget

Arithmetic from the values in the repo. Neither Gunicorn template uses `--threads` or a worker class, so every worker is a sync worker with one database connection.

| Process | Path | Value in repo | Connections |
|---|---|---|---|
| Gunicorn | Root-server (`setup.sh`) | `GUNICORN_WORKERS = min(2 × CPU + 1, 9)` (`setup.sh:324-326`), sync (`templates/gramaconnect-gunicorn.service:17-24`) | ≤ 9 |
| Gunicorn | Production on the hosting panel | `--workers ${WEB_WORKERS:-3}` (`deploy/qa/BVT-start.sh:91-93`) | 3 |
| Django runserver | qa (`DEBUG=False`, not production) | `manage.py runserver --noreload`, threaded, one thread per request (`BVT-start.sh:95`) | one per concurrent request; see BE-DB-011 |
| django-q2 cluster | all | `Q_WORKERS` default 2 (`settings.py:387`). django-q2 also runs a pusher (ORM `dequeue`, `brokers/orm.py:65-84`), a monitor and a sentinel that runs the scheduler (`scheduler.py:39-41`), each a separate process | about 5 (2 workers + pusher + monitor + sentinel; process count from the library design, not measured) |
| Backup | root-server and qa | `mysqldump --single-transaction` nightly at 02:30 (`backup.sh:29-32`; `gramaconnect-backup.timer:6-8`; `qa/BVT-backup.sh:24`) | 1 |
| Deploy and commands | all | `migrate`, `ensure_schedules`, `collectstatic` (`deploy.sh:92-99`; `qa/BVT-deploy.sh:77-81`), ad-hoc `manage.py` | 1 to 2 |

- **Root-server peak:** 9 + 5 + 1 + 2 = **17** connections.
- **Production panel peak:** 3 + 5 + 1 + 2 = **11** connections.
- **Database limit:** `max_connections` is **not set anywhere in the repo**; `setup.sh` writes no such line (`setup.sh:206-216`). The servers' vendor defaults apply, which could not be verified on the servers.

There is ample headroom at these settings. Headroom shrinks only if `Q_WORKERS`, `WEB_WORKERS` or a second cluster (for example qa and production on one database server) is added.

### 2.3 Connections held during slow work

- Sync Gunicorn workers keep their one connection for the whole request. Slow work in a request therefore holds a connection without holding a transaction:
  - Play Integrity verification on citizen registration (`citizens/services.py:167-179`, outside the transaction);
  - the Google Input Tools call (`web_admin/transliteration.py`);
  - video probing and re-encoding (`posts/media.py:174-245`);
  - the notification fan-out after commit (BE-DB-003).
- Two places do hold a **transaction** open during slow work:
  - **Video and photo files are written inside `transaction.atomic`** (`posts/media.py:137-138, 369-371`; `posts/services.py:474-497` calls `attach(post)` at :494-495). The insert's row locks stay held while multi-megabyte files are copied (BE-DB-015).
  - `translate_post` does the outside call **between** two autocommit statements (`posts/tasks.py:31-45`), which is correct.
- django-q2 workers hold a connection during FCM retries, which sleep inside the job (`grama/retry.py:39-48`; carries BE-PERF-005).

### 2.4 Connection findings

**BE-DB-010: MySQL `sql_mode` is replaced by `STRICT_TRANS_TABLES` alone**
- **Severity:** Low · **Effort:** S
- **Evidence:** `grama/settings.py:70` sets `sql_mode` to only `STRICT_TRANS_TABLES`. That replaces the server's whole default mode, including `ONLY_FULL_GROUP_BY`, `NO_ZERO_DATE`, `ERROR_FOR_DIVISION_BY_ZERO` and `NO_ENGINE_SUBSTITUTION`.
- **Impact:** MySQL then accepts some invalid data and ambiguous `GROUP BY` that PostgreSQL rejects. Bugs can hide on one engine.
- **Recommendation:** Keep the server's default mode, or add strict mode to it instead of replacing it. Recommend `TRADITIONAL` plus `ONLY_FULL_GROUP_BY`.

**BE-DB-011: The qa server runs threaded runserver with persistent connections**
- **Severity:** Low · **Effort:** S
- **Evidence:** `deploy/qa/BVT-start.sh:95` runs `runserver` when not production, with `DEBUG=False`, so `CONN_MAX_AGE=60` (`settings.py:74`). runserver starts a thread per request. Django's documentation advises against persistent connections there, because each thread opens its own connection, which is not reused.
- **Impact:** qa opens one connection per request thread, closed only when the thread object is collected or the server times it out. qa connection counts and behaviour are not representative of production (Gunicorn, sync). The number of connections was not measured.
- **Recommendation:** Run qa on Gunicorn as production does (as BE_02 also recommends), or set `DB_CONN_MAX_AGE=0` on qa.

**BE-DB-012: The production database's server settings are not in the repo**
- **Severity:** Info · **Effort:** S
- **Evidence:** Production runs on a hosting panel with a hand-made `DATABASE_URL` (`deploy/qa/BVT-deploy.sh:191-211`; carries BE-SEC-003). Its collation, `max_connections`, `wait_timeout`, slow-query log and engine version are not recorded anywhere in the repo.
- **Impact:** The utf8mb4 collation (Telugu correctness), the connection budget and the `CONN_MAX_AGE` vs `wait_timeout` relationship cannot be confirmed.
- **Recommendation:** Record the production database's version, charset, collation, `max_connections`, `wait_timeout` and slow-log state in `deploy/qa/README.md` or a runbook. Keep `CONN_MAX_AGE` below `wait_timeout`.

---

## 3. Query audit table

Risk is for state-wide data volume. "Index needed?" says whether a new or changed index would help; the existing indexes are listed in section 5.

| ID | File:line | Endpoint / task | Query description | Risk | Concern | Suggested improvement (conceptual) | Index needed? |
|---|---|---|---|---|---|---|---|
| Q-01 | `feed/views.py:49-70, 87-98, 123-151` | `GET feed/` | Published posts with an EXISTS on PostTarget for the village chain; four correlated subqueries for media; `Count("media")` join with GROUP BY; cursor on `(-published_at, -id)`; prefetch of translations and targets; describer batched | Medium | `Count("media")` forces a join and GROUP BY over every matching post before the LIMIT, so the index on `published_at` cannot stop early (BE-DB-008). Search is `icontains` on title and body (BE-DB-009). | Count media with a correlated subquery like the others; consider a stored media count | Yes: `(status, published_at)` |
| Q-02 | `feed/views.py:212-236` | `feed/upcoming/`, `feed/urgent/` | Same base, `event_start` or `is_urgent` filters, LIMIT 10 or 5 | Low | Same GROUP BY cost as Q-01, on a smaller set | As Q-01 | Existing `(status, event_start)` and `(status, is_urgent, published_at)` fit |
| Q-03 | `feed/views.py:155-206` | `feed/posts/<id>/`, `?ids=`, `share/<code>/` | Lookup by pk, by up to 50 ids, or by `share_code` (unique) | Low | `share_code` is matched case-insensitively on MySQL `_ci` (BE-DB-020) | Binary collation for `share_code` | No |
| Q-04 | `posts/views.py:50-53, 206-225`; `posts/services.py:187-190` | `GET posts/` | `visible_posts` (own OR EXISTS in reviewer areas), filters, status GROUP BY count, paginator COUNT, OFFSET page ordered by `-created_at, id`; prefetch media, translations, targets, review actions | Medium | Three scans per page (counts, COUNT, page). No index on `created_at`. `q` uses `icontains`. Citizen summary N+1 (BE-DB-005). | Index the ordering; cache or drop the per-page counts; keyset pagination for deep pages | Yes: `(created_at)` or `(status, created_at)` |
| Q-05 | `posts/views.py:374-393`; `posts/services.py:193-199` | `GET review/queue/` | PENDING posts, EXISTS in queue areas, ordered by `submitted_at, created_at`; then 2 more COUNTs for `source_counts` | Medium | Citizen summary N+1 (+3 per citizen post, measured); 3 counts per page | Batch the citizen summary; one grouped count | Existing `(status, submitted_at)` fits |
| Q-06 | `posts/serializers.py:245-264`; `posts/services.py:239-243` | Every PostSerializer row with a citizen post | Per distinct phone: `same_phone` posts COUNT, DISCARDED COUNT, `active_block`; `allowed_actions` calls `active_block` again | Medium | N+1 (BE-DB-005) | Compute per page with one grouped query | `android_id_hash` is indexed |
| Q-07 | `posts/views.py:403-428` | `GET stats/` | Five COUNTs over EXISTS-filtered posts and assignments | Low | Five scans; fine at area scale | Optional single conditional aggregate | No |
| Q-08 | `posts/views.py:104-105` | `GET admin/categories/` | `Count("posts")` per category | Low | Full scan of Post on the category FK index; small output | Acceptable; cache if slow | No |
| Q-09 | `posts/services.py:721-765` | Job `run_escalation`, every 15 min | All overdue PENDING posts, unbatched; per post: conditional UPDATE, reload, ReviewAction, audit, then fan-out after commit | Medium | Unbounded loop under a 120 s job timeout (BE-DB-016) | Batch with a LIMIT per run; move fan-out to a separate job | `(status, submitted_at)` serves the first branch; `escalated_at` is not indexed (BE-DB-007) |
| Q-10 | `notifications/services.py:49-56, 118-143` | After commit: submit, decide, escalate, service requests, Super Admin added | Per recipient: template SELECT, AreaDescriber queries, Notification INSERT, OrmQ INSERT | Medium | N+1 in the request thread (BE-DB-003; carries BE-PERF-001) | Load template and area once; `bulk_create`; one fan-out job | No |
| Q-11 | `notifications/views.py:89-112` | `GET notifications/` | Cursor on `(-created_at, -id)` for one user plus an unread COUNT | Low | The unread COUNT grows with an unbounded inbox (BE-DB-018) | Retention; bounded count | Existing `(user, created_at)` and `(user, read_at)` fit |
| Q-12 | `notifications/views.py:42-54, 69-83` | `POST devices/`, `PATCH devices/<id>/` | `update_or_create` on unique `fcm_token`; delete other rows with the token | Low | Two concurrent registrations of one token: the loser gets IntegrityError, which becomes a 500 (BE-DB-014) | Catch the conflict and re-read | `fcm_token` is unique |
| Q-13 | `audit/views.py:74-129` | `GET audit/` | Filters with 4 × `icontains`, area scope, OFFSET page by `-created_at`, paginator COUNT, plus GROUP BY `type` count | High | Unbounded table; two full counts per page for a Super Admin (BE-DB-002) | Keyset pagination; drop or cache counts; bounded date window by default; search on indexed fields | Yes, for the default view: `created_at` exists; search needs a different approach |
| Q-14 | `audit/views.py:158-180` | `GET audit/export/` | Up to 50,000 full rows (JSON `data` included) into memory, CSV built in a StringIO | High | Memory and time; over-fetching (BE-DB-002; carries BE-OBS-102) | Stream with `.iterator()`, fetch only the CSV columns, run large exports as a job | No |
| Q-15 | `web_admin/api.py:115-190` | `GET admin/dashboard/` | Per active district: EXISTS query plus `values_list` loaded into Python; 14-day lists; about 10 COUNTs; 31 queries on the fixture | High | Grows with districts; Python aggregation (BE-DB-001) | One grouped query with conditional aggregates; cache for a minute | Yes: `(status, submitted_at)` exists; PostTarget join via `(area_level, area_id, post)` |
| Q-16 | `web_admin/api.py:101-108, 196-221` | `admin/reports/summary/`, `posts-by-area/` (and CSV) | `posts_counts` loads every status in the window into Python; posts-by-area repeats it per district **and per mandal** (18 queries for 2 districts and 4 mandals) | High | Query count grows with mandals; all rows are pulled into Python (BE-DB-001; carries BE-PERF-002) | One GROUP BY over PostTarget joined to geography | Possibly `(created_at)` on Post (BE-DB-007) |
| Q-17 | `web_admin/api.py:224-312, 330-335` | `admin/reports/review-time/`, `admin-activity/` | All decisions in the window plus all their submissions into Python; all admin assignments loaded, then **paginated in Python** | Medium | Memory grows with the window and officials; the page is cut after loading everything | Aggregate in SQL; paginate in the database | `ReviewAction(created_at)` exists; `action` is not indexed (filtered together with `created_at`) |
| Q-18 | `accounts/views_users.py:135-235` | `GET users/` | `manageable_users` (IN and NOT IN subqueries), `icontains` on name, Telugu name, username, email and `contains` on mobile; 4 extra COUNTs; OFFSET page; prefetch assignments | Low | Official counts stay small; email and mobile search scans | Acceptable at the expected size; consider prefix search | No |
| Q-19 | `service_requests/views.py:110-141`; `serializers.py:176-196`; `services.py:67-85` | `GET service-requests/` | `visible_requests` area scope; OFFSET page by `-updated_at`; status GROUP BY; per row `allowed_actions` loads village ancestry (not primed) and the assignee's grants | Medium | N+1 (BE-DB-006); no index on the ordering | Prime ancestry; batch assignee checks; index the ordering | Yes: `(updated_at)` or `(village, updated_at)` |
| Q-20 | `citizens/services.py:105-133, 238-264`; `service_requests/services.py:204-212` | Citizen token check, posting limit, request limit | Lookup by unique `token_hash`; `same_phone` OR on `android_id_hash`; COUNT of the last 24 h, then insert | Medium | Check-then-insert races (BE-DB-014) | Database-backed counter or locked row per phone | Existing indexes fit |
| Q-21 | `geography/views.py:101-119` | `GET geo/villages/?q=` (public, no throttle) | `icontains` on `name_en` OR `name_te` across all active villages (3 joins for ancestors' `is_active`), ORDER BY name, LIMIT 50 | Medium | A full scan of all villages for every search, not throttled (BE-DB-009; carries BE-PERF-007, BE-SEC-006) | Prefix search on `name_key` and a Telugu key; throttle | Yes, if prefix search is adopted |
| Q-22 | `geography/nearest.py:49-85` | `GET geo/nearest/` | Bounding box on `latitude`/`longitude`, haversine in Python; radius doubles 5, 10, 20, 40, 50 km | Low | At most 5 queries per call; the 50 km box can load every village inside it; the composite index serves the latitude range only | Acceptable; throttle the anonymous endpoint | Existing `(latitude, longitude)` |
| Q-23 | `geography/views_admin.py:197-227` | `GET geo/tree/` | Loads every village's `mandal_id` to count in Python; with `?villages=1` loads every Village object | Low | Code says 17,000 villages (`views_admin.py:184-187`) | GROUP BY count in SQL; only needed columns | No |
| Q-24 | `contacts/selectors.py:26-31, 40-44` | `GET contacts/`, admin list | Village chain OR, `select_related("type")`; admin list by `area_q` | Low | Bounded by `CONTACTS_MAX_PER_AREA` per area | n/a | Existing `(is_active, area_level, area_id)` |
| Q-25 | `accounts/authentication.py:72-119`; `accounts/tokens.py:38-46` | Every authenticated request | One User SELECT per JWT request; session requests also save the session row (`SESSION_SAVE_EVERY_REQUEST`, `settings.py:304`); authz loads 2 queries once per request (`authz.py:107-159`) | Low | One UPDATE of `django_session` per console request (BE-DB-026) | Acceptable; it is what gives the 30-minute idle timeout | No |
| Q-26 | `posts/services.py:99-106` | `runtime()`: app config, citizen posting, escalation | One SettingOverride SELECT per call, uncached | Low | Repeated small queries | Short per-process cache | `key` is unique |
| Q-27 | `grama/housekeeping.py:27-49` | Daily housekeeping job | Unbatched deletes on `Failure`, `OutstandingToken` (cascading to `BlacklistedToken`), `Session`, `AccessFailureLog` | Low | Large first-run deletes; cascading deletes load rows in Python (BE-DB-019) | Delete in batches | No |
| Q-28 | `django_q/brokers/orm.py:65-84` (installed) | django-q2 pusher | `SELECT … WHERE key=? AND lock<now LIMIT 10`, then conditional UPDATE per task; `sleep(poll)` when empty | Low | `poll` not set, so 0.2 s, about 5 SELECTs a second while idle (BE-DB-017) | Set `poll` to 1–2 s | OrmQ has no index on `(key, lock)` (library); the table stays small |

---

## 4. N+1 findings

**BE-DB-003: Notification fan-out runs several queries per recipient, in the request thread (carries BE-PERF-001)**
- **Severity:** Medium · **Effort:** M
- **Evidence:**
  - `notifications/services.py:118-143`. For each user: `render()` reads `NotificationTemplate` again (`:49-56`), `_values()` builds a new `AreaDescriber` and queries the area (`:98-105`), there is one `Notification.objects.create`, and one `async_task` (an `OrmQ` INSERT).
  - `tell_super_admins` (`:220-245`) and `notify_service_request` (`:286-300`) do the same.
  - The receivers run in `transaction.on_commit` (`posts/services.py:280-281`, `notifications/receivers.py:25-60`), so they run inside the HTTP request after commit.
  - **Measured:** 9 queries for 1 recipient and 54 for 6, so +9 per recipient. That includes the push job, because tests run django-q `sync` (`settings.py:395`).
- **Impact:**
  - A post for many areas, or a state-level post that pings Super Admins, multiplies writes and request time.
  - Each insert commits on its own (autocommit after commit), so a crash mid-loop leaves some reviewers notified and others not.
- **Recommendation:**
  - Load the template and area names once per call.
  - Create the inbox rows with one bulk insert.
  - Queue a single job that fans out pushes, or move the whole fan-out into one job.

**BE-DB-005: The citizen summary in every post row costs 3 queries per citizen phone**
- **Severity:** Medium · **Effort:** S
- **Evidence:**
  - `posts/serializers.py:245-264`: per distinct `citizen_id`, it runs `same_phone` posts `.count()`, a DISCARDED `.count()` and `active_block()`. The cache is per response only.
  - `allowed_actions` calls `active_block(post.citizen)` again (`posts/services.py:239-243`).
  - **Measured:** the review queue went from 17 to 44 queries and `posts/` from 16 to 43 when citizen posts went from 1 to 10. The existing test that checks query counts stay flat uses officials' posts only (`accounts/tests/test_authz.py:317-332`).
- **Impact:** The console's citizen tab, which the lead asked for (`posts/views.py:146-153`), slows linearly with page size (up to 100 rows per page).
- **Recommendation:**
  - Compute the totals, discards and active blocks for all phones on the page in one or two grouped queries, keyed by the Android-ID hash.
  - Add a query-count test with citizen posts.

**BE-DB-006: `allowed_actions` on service requests loads area ancestry and the assignee's grants per row**
- **Severity:** Medium · **Effort:** S
- **Evidence:**
  - `service_requests/services.py:67-85` calls `authz.can(user, VIEW, area)` per request; the village ancestry is not primed (`authz.py:198-202`).
  - `_holder_id` calls `authz.can(assignee, …)` on each row's own `assigned_to` object. Each is a separate Python object from `select_related`, so `authz._load` runs 2 queries per row (`authz.py:107-159`).
  - Nothing in `service_requests/` calls `prime`.
  - **Measured:** 12 queries for 1 row and 30 for 8.
- **Impact:** Officials' service-request lists grow by about 2.6 queries per row, and up to 100 rows fit on one page.
- **Recommendation:** Prime ancestry for the page's villages, and check assignees with one query that names the users still holding the permission over each village.

**BE-DB-001 (part): Console dashboard and reports query per district and per mandal.** See section 6 and BE-DB-001 in section 7, which covers the full finding.

**Covered well (no N+1):**
- The feed and its short lists use subqueries, prefetches and one `AreaDescriber` per page; tests keep them at most 8 queries (`feed/tests/test_feed.py:191-194`, `feed/tests/test_feed_owner_decisions.py:173-178`).
- The posts and users lists use `prime_posts`, `prime_describer` and `authz.prime` (`posts/views.py:221-222`, `accounts/views_users.py:143-149`).
- Django admin uses `list_select_related` for geography (BE_01).

---

## 5. Index review

"Exists" means a declared index, a unique constraint, a primary key, or the automatic index on a ForeignKey.

| Model | Field(s) | Used in | Index exists? | Evidence |
|---|---|---|---|---|
| Post | `status, target_level, target_area_id, published_at` | Formerly the feed. **The feed now filters through PostTarget**, so nothing filters on `target_level`/`target_area_id` with `status` first | Yes, but **probably unused** | `posts/models.py:114`; `feed/views.py:92-98` |
| Post | `status, published_at` (feed ordering) | `feed/` cursor `-published_at, -id` after `status=PUBLISHED` | Partly: `published_at` alone (`db_index`) | `posts/models.py:93` |
| Post | `status, submitted_at` | Review queue order, escalation, dashboard | Yes | `posts/models.py:115` |
| Post | `author, status` | `visible_posts` own posts, `mine=` | Yes | `posts/models.py:116` |
| Post | `status, event_start` | `feed/upcoming/` | Yes | `posts/models.py:117` |
| Post | `status, is_urgent, published_at` | `feed/urgent/` | Yes | `posts/models.py:118` |
| Post | `created_at` | `posts/` ordering `-created_at, id` (`posts/views.py:220`); reports `created_at__gte` (`web_admin/api.py:196-198`); `posts_today` (`citizens/services.py:241`) | **No** | `posts/models.py:90, 111-119` |
| Post | `escalated_at` | Escalation `escalated_at__lte` (`posts/services.py:732-734`), `escalated=` filter | **No** (filtered together with `status=PENDING`) | `posts/models.py:99` |
| Post | `citizen` | `same_phone` posts, limits | Yes (FK) | `posts/models.py:76-78` |
| Post | `category` | Category filter, annotate | Yes (FK) | `posts/models.py:83` |
| Post | `share_code` | `/p/<code>/`, `feed/share/` | Yes (unique) | `posts/models.py:109` |
| PostTarget | `area_level, area_id, post` | Feed EXISTS, queue EXISTS, `targets_within` | Yes | `posts/models.py:276` |
| PostTarget | `post, area_level, area_id` | Prefetch, `_set_targets` delete | Yes (unique) | `posts/models.py:273-275` |
| PostMedia | `post` (plus order) | Feed subqueries, prefetch | Yes (FK) | `posts/models.py:204` |
| PostTranslation | `post, language` | Prefetch, `update_or_create` | Yes (unique) | `posts/models.py:303` |
| ReviewAction | `post, created_at` | History, `latest_comment` | Yes | `posts/models.py:255` |
| ReviewAction | `created_at` (plus `action__in`) | Reports | Yes (`created_at` `db_index`); `action` not indexed | `posts/models.py:251` |
| Notification | `user, created_at` | Inbox cursor | Yes | `notifications/models.py:74` |
| Notification | `user, read_at` | Unread count, mark all read | Yes | `notifications/models.py:75` |
| Device | `fcm_token` / `user` / `village_id` | Register, push to user | Yes (unique, index, index) | `notifications/models.py:24, 36` |
| AuditLog | `created_at` | List and export ordering, date filters | Yes | `audit/models.py:41` |
| AuditLog | `type, created_at` / `actor, created_at` / `area_level, area_id` / `target_type, target_id` / `action` | Type tab, actor filter, area scope, target, action filter | Yes | `audit/models.py:47-48, 65-70` |
| AuditLog | `actor_name`, `target_label`, `actor_role` (`icontains`) | `q=` search | **No** (and a B-tree would not help `%x%`) | `audit/views.py:79-84` |
| User | `username`, `mobile` | Login, duplicate checks | Yes (unique) | `accounts/models.py:85-101` |
| User | `is_active` | Counts, holders | Yes | `accounts/models.py:104-109` |
| RoleAssignment | `user, group, area_level, area_id` | Uniqueness, `_load` by user | Yes (unique, leading `user`) | `accounts/models.py:265-269` |
| RoleAssignment | `area_level, area_id` / `group, area_level, area_id` | `area_q`, `manageable_users`, `holders` | Yes | `accounts/models.py:270-273` |
| Village | `latitude, longitude` | Nearest village | Yes | `geography/models.py:195` |
| Village / Mandal / District | `parent, name_key` | Unique names, import matching | Yes (unique) | `geography/models.py:124, 141, 196` |
| Village | `name_en`, `name_te` (`icontains`) | Public search | **No** | `geography/views.py:116-117` |
| CitizenDevice | `token_hash` / `nonce` / `android_id_hash` / `registered_ip_hash, created_at` / `created_at` | Token check, nonce reuse, `same_phone`, IP limit | Yes | `citizens/models.py:18, 24, 27, 32, 35, 46` |
| ServiceRequest | `village, status` / `assigned_to, status` / `device, created_at` | Area scope, `assigned=me`, daily limit | Yes | `service_requests/models.py:123-127` |
| ServiceRequest | `updated_at` | List ordering `-updated_at, -id` | **No** | `service_requests/models.py:122`; `views.py:131` |
| Contact | `is_active, area_level, area_id` / `type, is_active` / `phone` | Citizen read, type use, duplicate check | Yes | `contacts/models.py:106-110` |
| SettingOverride / Category / NotificationTemplate / Service | `key` | Lookups | Yes (unique) | `posts/models.py:309, 25`; `notifications/models.py:102`; `service_requests/models.py:45` |
| django_q OrmQ | `key, lock` | Broker poll | **No** (third-party model) | `.venv/.../django_q/models.py:426-440` |

**BE-DB-007: List orderings and range filters without matching indexes; one Post index is stale**
- **Severity:** Medium · **Effort:** S (new migration)
- **Evidence:**
  - `Post.created_at` has no index (`posts/models.py:90, 111-119`), yet orders `GET posts/` (`posts/views.py:220`) and filters the reports' window (`web_admin/api.py:196-198`) and the citizen daily limit (`citizens/services.py:241`).
  - `ServiceRequest.updated_at` orders its list (`service_requests/views.py:131`) without an index.
  - The feed no longer filters on `Post.target_level/target_area_id`, so the index at `posts/models.py:114` serves nothing found in the code.
- **Impact:** A Super Admin's `posts/` (scope `Q()`) and the reports sort or scan the whole posts table on every page. The stale index costs write time on every post update.
- **Recommendation:** In a new migration:
  - add `(status, published_at)` and `(created_at)` or `(status, created_at)` on Post;
  - add `(updated_at)` on ServiceRequest;
  - after confirming with `EXPLAIN` on the chosen engine, drop the unused `(status, target_level, target_area_id, published_at)`.
  - On PostgreSQL, build the new indexes concurrently.

---

## 6. Pagination and count findings

| Endpoint | Pagination | Counts per page | Evidence |
|---|---|---|---|
| `feed/` | Cursor `(-published_at, -id)`, 20 | none | `feed/views.py:41-46` (good) |
| `notifications/` | Cursor `(-created_at, -id)`, 20 | 1 (unread) | `notifications/views.py:89-112` (good) |
| `posts/` | Page number (OFFSET), up to 100 | paginator COUNT plus status GROUP BY | `posts/views.py:206-225`; `grama/pagination.py:4-9` |
| `review/queue/` | Page number | COUNT plus 2 `source_counts` | `posts/views.py:387-393, 156-157` |
| `audit/` | Page number | COUNT plus type GROUP BY | `audit/views.py:114-129` |
| `users/` | Page number | COUNT plus total, active, blocked, by-role (4) | `accounts/views_users.py:201-235` |
| `service-requests/` | Page number | COUNT plus status GROUP BY | `service_requests/views.py:133-141` |
| `admin/reports/admin-activity/` | **Python list sliced by the paginator** | everything loaded | `web_admin/api.py:330-335` |

**BE-DB-002: The audit log list and export do not scale with an append-only table**
- **Severity:** High · **Effort:** M
- **Evidence:**
  - AuditLog is never cleaned (`audit/models.py:75-81`, `grama/housekeeping.py:13-14`).
  - The list uses page-number OFFSET plus the paginator `COUNT(*)` and a second `GROUP BY type` count over the same filtered set (`audit/views.py:114-129`).
  - For a platform-wide reader the scope is `Q()` (`authz.py:250-251`), so both counts cover the whole table.
  - `q=` adds four `icontains` filters (`audit/views.py:79-84`).
  - The export iterates up to 50,000 full model rows, including the JSON `data` the CSV never uses, into a StringIO and then one HttpResponse string (`audit/views.py:166-180`).
- **Impact:** Every view of the console audit page costs two full counts that grow without limit. Deep pages pay OFFSET scans. The export can run for a long time and use a lot of memory per Gunicorn worker. That matters with 3 workers on the panel and a 60 s nginx limit (`deploy/RUNBOOK.md:51`).
- **Recommendation:**
  - Use cursor or keyset pagination on `(created_at, id)`.
  - Default to a bounded date window, and show counts for that window, or as estimates.
  - Make search match exact or prefix values on indexed columns.
  - For export, stream only the needed columns with an iterator, or produce the file in a background job.

**BE-DB-009: OFFSET pages with several COUNTs on growing tables, and unindexed text search**
- **Severity:** Medium · **Effort:** M
- **Evidence:**
  - `posts/views.py:206-225` (two scans plus the page);
  - `posts/views.py:392`, `156-157` (queue: three counts);
  - `service_requests/views.py:133-141`.
  - Search uses `icontains`:
    - feed (public) `feed/views.py:133-141`;
    - posts `posts/views.py:200-202`;
    - villages (public, unthrottled) `geography/views.py:116-117`;
    - service requests `service_requests/views.py:116-121`;
    - users `accounts/views_users.py:176-184`.
- **Impact:**
  - Posts, queue and service-request lists do three or four scans per page.
  - `icontains` cannot use a B-tree index, so each search is a scan of the candidate rows.
  - Telugu text compares under `utf8mb4_unicode_ci` on MySQL but byte-for-byte on SQLite, so results can differ by engine (BE-DB-021).
- **Recommendation:**
  - Fetch tab counts in one grouped query, cached briefly, or on demand.
  - Move the busiest lists to keyset pagination.
  - For search, use prefix matching on normalised keys, or a full-text index once the engine is chosen.
  - Throttle the anonymous searches.

**BE-DB-008: The feed's `Count("media")` turns every page into a GROUP BY**
- **Severity:** Medium · **Effort:** S
- **Evidence:** `feed/views.py:65`: `media_count=Count("media")` on a queryset that is also cursor-ordered and limited (`:41-46`). The other media values already use correlated subqueries (`:50-68`).
- **Impact:** The database must join PostMedia and group all published posts for the village chain (every state-level post included) before it can sort and return 20. The cost grows with total published posts, not page size. This is the citizen app's busiest endpoint, on 2G phones.
- **Recommendation:** Count media with a correlated subquery like the other annotations, or keep a count column on Post that the media service updates. Confirm with `EXPLAIN` that the plan walks `published_at` and stops at the LIMIT.

**BE-DB-001: Dashboard and reports query per area and aggregate in Python (carries BE-PERF-002)**
- **Severity:** High · **Effort:** M
- **Evidence:**
  - `web_admin/api.py:127-139`: the dashboard loops over every active district, running one EXISTS-filtered query per district and loading `submitted_at` values into Python.
  - `:146-158`: two 15-day value lists are bucketed in Python, and one list is read twice.
  - `:101-108`: `posts_counts` loads every status in the window into a Python list and counts with `len`/`sum`.
  - `:205-221`: `posts-by-area` calls it once per district **and once per mandal**. The window can be 366 days (`:75-79`).
  - `:274-312`: `admin-activity` loads every admin assignment and all decisions, and is paginated in Python (`:330-335`).
  - `ReportExportView` repeats the same work for CSV (`:377-411`).
  - **Measured on the 2-district, 4-mandal fixture:** dashboard 31 queries, posts-by-area 18.
- **Impact:**
  - The query count of posts-by-area is about (districts + mandals + constant) for any area count, and each query transfers every status row of its area for the window.
  - The code and docs expect 17,000–18,000 villages (`geography/views_admin.py:184-187`; `.claude/architecture.md`, "a state's 18,000 villages"), so state-wide data means hundreds of queries and large transfers per click.
  - Gunicorn allows 300 s, but nginx cuts API calls at 60 s (`deploy/RUNBOOK.md:51`). The console can see 504s while the worker keeps running.
- **Recommendation:**
  - Replace the loops with one grouped query per report: PostTarget joined to Mandal and District, with conditional counts per status. Bucket dates with database date functions that are portable, or keep Python bucketing over pre-grouped rows.
  - Paginate admin-activity in SQL.
  - Cache the dashboard for 30–60 s.

---

## 7. Write-path and concurrency findings

**BE-DB-004: The "never zero" and "at most N" Super Admin rules are check-then-act without a lock**
- **Severity:** Medium · **Effort:** M
- **Evidence:**
  - `accounts/peers.py:52-81` counts active holders, then the caller writes in a separate step (`views_users.py:306-315, 322-328, 428-466`). The check runs before the `transaction.atomic()` block, and nothing locks a shared row.
- **Impact:**
  - If two Super Admins block each other at the same moment, both checks see another active holder, both blocks commit, and the platform has **zero active Super Admins**. Recovery then needs `create_super_admin` on the server.
  - Similarly, two simultaneous grants can exceed `MAX_SUPER_ADMINS`.
- **Recommendation:**
  - Serialise peer-role changes on one row, for example by locking the Super Admin `RoleGroup` row inside the transaction.
  - Re-check after the write, inside the same transaction, and roll back if the rule broke. `contacts/services.py:343-371` already uses this pattern.

**BE-DB-014: Count-then-insert limits and check-then-insert uniqueness (carries BE-COR-002)**
- **Severity:** Medium · **Effort:** M
- **Evidence:**
  - Citizen posts per day: `citizens/services.py:257-264`.
  - Registrations per IP: `:160-165`.
  - Service requests per day: `service_requests/services.py:204-212`.
  - Numbers per area: `contacts/services.py:155`.
  - Photos per post: `posts/media.py:130`, where the order is also taken as `post.media.count()` at `:138`.
  - Username and mobile pre-checks: `accounts/views_users.py:255-258`. They are backed by unique constraints (`accounts/models.py:85-101`), but the IntegrityError from a race is not caught there, so it becomes a 500.
  - The service and bill key loops: `service_requests/services.py:107-112`, `bills/services.py:35`.
  - Device registration `update_or_create` on unique `fcm_token`: `notifications/views.py:53`.
- **Impact:**
  - Parallel requests can exceed a limit by the number of concurrent requests. Two photos can get the same `order`, and a post can exceed `MAX_PHOTOS_PER_POST`.
  - Unique-guarded races answer 500 instead of 409.
- **Recommendation:**
  - Lock the parent row for the check (the Post for photos, the CitizenDevice for limits) inside the transaction, or re-count after the insert and roll back.
  - Catch IntegrityError on unique-backed creates and answer the contract's `duplicate`.

**BE-DB-020: Random share codes meet a case-insensitive collation on MySQL**
- **Severity:** Low · **Effort:** S
- **Evidence:**
  - `posts/sharing.py:17-27` builds codes from 62 mixed-case characters and relies on the unique column "as the real guard".
  - With `utf8mb4_unicode_ci` (`settings.py:72`, `setup.sh:212, 222`), `aB3…` and `Ab3…` are equal, so the unique index and the lookups (`feed/views.py:203`, `share/views.py`) ignore case.
  - Migration `0009` checked uniqueness in a case-sensitive Python set (`posts/migrations/0009_post_share_code.py:14-19`).
  - A collision raises IntegrityError inside `review`/`_move_on_submit`, which has no retry (`posts/services.py:346-355, 613-618`).
- **Impact:** The code space shrinks from 62⁸ to about 36⁸ on MySQL. A rare publish fails with a 500, and the migration could fail on a clash. SQLite tests compare case-sensitively, so none of this is tested.
- **Recommendation:** Store `share_code` with a binary or case-sensitive collation, or generate codes from one case only. Retry once on a unique conflict.

**Other write-path notes (Low)**

**BE-DB-022: Small race windows**
- **Severity:** Low · **Effort:** S
- **Evidence:**
  1. A refresh token can be used twice by two concurrent refresh calls. The blacklist check (at construction) and the blacklist write (`accounts/tokens.py:49-64`) are separate steps.
  2. `update_request` saves fields from the instance it loaded, not with a conditional UPDATE, and audits `before` from that copy (`service_requests/services.py:282-304`).
  3. `Schedule.objects.update_or_create(name=…)` has no unique `name` in django-q2 (`.venv/.../django_q/models.py:233-239`), so two simultaneous `ensure_schedules` runs could create duplicate schedules (`posts/tasks.py:84-95`, `grama/housekeeping.py:52-58`).
- **Impact:** Duplicate sessions, or a stale "from" status in audit; a duplicate escalation schedule would run escalation twice. Each needs concurrent timing.
- **Recommendation:** Treat these as hardening: a conditional update for the refresh token's blacklist row, a conditional update on status for service requests, and a single deploy-time lock for `ensure_schedules`.

---

## 8. Transaction findings

**Done well**
- Every post move is one conditional UPDATE followed by ReviewAction and audit in the same `atomic()` block (`posts/services.py:617-631, 665-677, 741-763`).
- Signals are queued with `transaction.on_commit` (`:280-281`). Service requests do the same (`service_requests/services.py:92-101`), and so does the peer signal (`accounts/views_users.py:89-93`).
- Logo and ad files are deleted only after commit (`ads/services.py:92`; `grama/logos.py`).
- The bulk geography import writes inside one transaction in batches (`geography/services.py:187-191`).

**BE-DB-015: Media files are written inside database transactions**
- **Severity:** Low · **Effort:** M
- **Evidence:**
  - `posts/media.py:137-138` and `:369-371` wrap `store_photo`/`store_video`, which write three or four files each (`:141-148, 340-358`), in `atomic()`.
  - Citizen posts write all photos and videos inside `create_citizen_post`'s transaction (`posts/services.py:474-497`).
  - `delete_post` removes image, medium and thumbnail but **not `video`** (`posts/services.py:558-561`; carries BE-COR-003).
- **Impact:**
  - On rollback (a constraint error, or a crash) the files stay on disk with no row.
  - The transaction, and its row locks on the new Post, PostTarget and PostMedia rows, stays open while large files are copied.
  - Deleted drafts leave video files behind.
- **Recommendation:**
  - Write files before the transaction and register an `on_commit`/rollback clean-up, or write them to a staging name and move them after commit.
  - Delete the video file along with the other renditions.

**BE-DB-023: Multi-row admin actions are not atomic as a whole**
- **Severity:** Low · **Effort:** S
- **Evidence:**
  - `citizens/services.py:324-341` commits the block, then discards pending posts one by one outside that transaction (`:307-321`).
  - `discard_all` (`:360-370`) records its audit entry after the loop, outside any transaction.
  - `geography`'s import is atomic, but user-group sync runs in `post_save` and `post_delete` signals inside the caller's transaction (`accounts/signals.py:50-74`). That is acceptable, but adds 2–4 queries per assignment write.
- **Impact:** A crash mid-way leaves a phone blocked with some posts not discarded, and the summary audit entry is missing. Each post's own discard is still consistent.
- **Recommendation:** Wrap block plus discards (or discard-all plus its audit) in one transaction. Alternatively, accept the partial result and record the summary first.

**Long transactions and deadlocks**
- No long transactions were found apart from BE-DB-015.
- `select_for_update` is used only in contacts (`contacts/services.py:63, 350, 533`), each time on one ContactType row and in one order. The ORM broker and scheduler use a conditional UPDATE, or `select_for_update` on `Schedule` alone (`django_q/scheduler.py:39-41`).
- No inconsistent lock order was found.
- On MySQL, the conditional UPDATEs under READ COMMITTED lock only the matching row.

---

## 9. Migration risk findings

**Drift:** `makemigrations --check --dry-run` gives "No changes detected". There are 56 migration files across 13 apps.

**BE-DB-013: Deploys migrate in place, with no backup first; MySQL DDL is not transactional**
- **Severity:** Medium · **Effort:** S
- **Evidence:**
  - `deploy/deploy.sh:92-99` and `deploy/qa/BVT-deploy.sh:77-78` run `migrate` while the old code is still serving, and restart afterwards (`deploy.sh:130-132`). Neither script takes a backup before migrating; the backup runs nightly (`backup.sh`).
  - On MySQL every DDL statement auto-commits, so a migration that fails part-way (for example a `RunPython` after an `AddField`, as in `posts/0009`) stays half applied.
- **Impact:**
  - A failed migration on MySQL can leave the schema between two versions, and the old code is serving against it.
  - The only restore point can be up to 24 hours old.
- **Recommendation:**
  - Take a database dump right before `migrate` in both deploy scripts.
  - Keep schema and data steps in separate migrations.
  - Document the manual recovery for a half-applied migration on MySQL.

**BE-DB-024: Irreversible data migrations, and per-row data steps**
- **Severity:** Low · **Effort:** S (for future migrations; merged ones must not be edited)
- **Evidence:**
  - `RunPython(..., migrations.RunPython.noop)` with no reverse: `accounts/0002`, `accounts/0008`, `bills/0003`, `notifications/0003`, `notifications/0008`, `posts/0002`, `posts/0008`, `posts/0009`, `service_requests/0002`.
  - `posts/0009` issues one UPDATE per published post (`posts/migrations/0009_post_share_code.py:15-20`).
  - Good example: `posts/0008` batches with an iterator and `bulk_create` (`posts/migrations/0008_post_targets.py:7-17`).
- **Impact:** These migrations cannot be rolled back with `migrate <app> <previous>`; rollback means restoring a backup. Per-row data steps take time in proportion to the table.
- **Recommendation:** For future data migrations, write a reverse step where one exists, batch updates, and note "irreversible" in the PR. Do not edit the merged ones.

**Large-table DDL**
- None of the existing migrations add an index or column to a table that is large *today*. That cannot be verified on production (no row counts).
- Future index additions (BE-DB-007) on `posts_post`, `audit_auditlog` and `notifications_notification`:
  - On PostgreSQL, build concurrently, with a non-atomic migration.
  - On MySQL 8, `ADD INDEX` is online (in-place). A column type change copies the table.
  - Schedule such changes outside peak hours and away from the 02:30 backup.

---

## 10. Background job findings

Configuration (`settings.py:384-396`):
- ORM broker, 2 workers, `timeout` 120, `retry` 180 (correctly larger than `timeout`), `max_attempts` 3, `queue_limit` 50, `bulk` 10, `catch_up` False.
- Not set, so library defaults apply:
  - `poll` 0.2 s (`django_q/conf.py:93`);
  - `save_limit` 250 (`:114`);
  - `ack_failures` False (`:172`).

| Job | Schedule or trigger | Idempotent? | On failure | Evidence |
|---|---|---|---|---|
| `send_push_to_topics` | per publish | **No.** A re-run after a timeout re-sends to every topic (carries BE-REL-101) | Retries sleep in the worker; final failure audited | `notifications/tasks.py:31-46`; `grama/retry.py:29-49` |
| `send_push_to_user` / `send_push_to_citizen` | per recipient | Mostly. A re-run re-sends | Invalid token deleted or cleared | `notifications/tasks.py:49-85` |
| `translate_post` | on publish | Yes (`source_hash` check; `update_or_create` on unique `(post, language)`) | One-off schedules at 1, 5 and 15 min; no sleeping | `posts/tasks.py:18-66` (good) |
| `run_escalation` | every `ESCALATION_CHECK_MINUTES` (15) | Yes per post (conditional UPDATE on `escalated_at`) | Unbatched; see BE-DB-016 | `posts/services.py:721-765` |
| `run_housekeeping` | daily | Yes | Unbatched deletes; see BE-DB-019 | `grama/housekeeping.py:27-58` |

**BE-DB-016: The escalation run is unbatched under a 120 s timeout, and alerts fan out inside it**
- **Severity:** Medium · **Effort:** S
- **Evidence:**
  - `posts/services.py:732-765` iterates every overdue PENDING post with no LIMIT. For each post it opens a transaction, runs a conditional UPDATE, a reload, a ReviewAction and an audit entry, and after commit `alert_escalation`, which runs the fan-out of BE-DB-003.
  - The job timeout is 120 s (`settings.py:388`).
- **Impact:**
  - After an outage, or for a backlog of many pending posts, one run can exceed 120 s and be killed. Posts committed before the kill are escalated, but if the kill lands between a commit and its `on_commit` alert, that alert is lost: the post is marked escalated and never re-alerted for that level.
  - The other worker, the only other one, is busy meanwhile.
- **Recommendation:**
  - Process a bounded batch per run, oldest first.
  - Queue the alert as its own job per post, inside the same transaction as the escalation, so it commits with it.

**BE-DB-017: The broker polls 5 times a second when idle**
- **Severity:** Low · **Effort:** S
- **Evidence:**
  - `poll` is not set in `Q_CLUSTER` (`settings.py:384-396`), so `Conf.POLL = 0.2` (`django_q/conf.py:93`).
  - The ORM broker runs a `SELECT` on `django_q_ormq`, then `sleep(Conf.POLL)` when empty (`brokers/orm.py:65-84`), after `close_old_connections()` (`:22`).
  - From the config, that is up to 5 queries per second per cluster while idle, about 432,000 a day. This is arithmetic, not a measurement.
- **Impact:** Constant small load and noise in any slow or general query log. On a shared hosting panel database, it may count against limits.
- **Recommendation:** Set `poll` to 1–2 s. Push latency stays acceptable for this product.

**Overlaps**
- The 02:30 backups (`gramaconnect-backup.timer:6-8`, `qa/BVT-backup.sh:24`) use `--single-transaction` (`backup.sh:30`), so they do not block jobs on InnoDB.
- Housekeeping is `Schedule.DAILY` with no fixed time; its first `next_run` is when the schedule was created (`grama/housekeeping.py:52-58`), so its hour is whatever the first deploy's hour was. It could coincide with the backup.
- Escalation cannot overlap itself: 120 s timeout against a 15 min interval.

**Task tables**
- Successes are capped at 250 by django-q2. Failures are deleted after `HOUSEKEEPING_KEEP_DAYS` (30; `settings.py:427`).
- `OrmQ` rows are deleted on acknowledge (`brokers/orm.py`, `acknowledge` calls `delete`).
- Task rows keep their arguments, which hold titles and names (carries BE-SEC-108).

---

## 11. Data growth and retention

| Table | Grows with | Cleaned? | Evidence |
|---|---|---|---|
| `audit_auditlog` | every change, login, export | **Never, by design** | `audit/models.py:75-81`; `grama/housekeeping.py:13-14` |
| `notifications_notification` | every review ping, decision, escalation, service request | **No** | no delete found in `apps/` |
| `posts_reviewaction` | every post move and escalation | No (kept with the post) | `posts/models.py:229-255` |
| `service_requests_servicerequestevent` | every request change | No | `service_requests/models.py:138-158` |
| `citizens_citizendevice` | every app install (re-installs add rows) | **No** | no delete in `apps/` |
| `notifications_device` | every install that registers (citizens and officials) | Only officials' invalid tokens, on a failed push (`notifications/tasks.py:75-80`); citizens' topic devices are never pushed individually, so **never pruned** | `notifications/views.py:42-54` |
| `web_public_contactmessage` | website contact form | No | BE_01 inventory; no delete found |
| `posts_post` / `posts_postmedia` (plus files) | content | Only draft deletion | `posts/services.py:549-561` |
| `django_q_task` (success / failure) | jobs | 250 successes kept; failures 30 days | `django_q/conf.py:114`; `grama/housekeeping.py:36` |
| `django_session`, simplejwt outstanding/blacklisted, axes `AccessFailureLog` | logins | Yes, daily | `grama/housekeeping.py:36-43` |
| `grama_throttle_cache` | throttle keys | Culled by Django's DB cache, which runs `SELECT COUNT(*)` on each set | `settings.py:156`; `django/core/cache/backends/db.py:113-121` |

**BE-DB-018: No retention for most user-driven tables**
- **Severity:** Medium · **Effort:** M
- **Evidence:** the table above.
- **Impact:**
  - Inbox counts and audit views slow down over time (BE-DB-002, Q-11).
  - Dead FCM tokens and abandoned citizen installs accumulate.
  - Personal data is kept with no end date (carries BE-DP-101).
- **Recommendation:** After an owner decision on retention periods:
  - delete read notifications after N days;
  - prune `Device` rows not seen for N days and `CitizenDevice` rows with no posts, requests or block after N days;
  - archive contact messages.
  - Consider yearly audit partitions or an archive table; the audit log's immutability makes this a policy decision, not a code change.

**BE-DB-019: Housekeeping deletes in single statements**
- **Severity:** Low · **Effort:** S
- **Evidence:** `grama/housekeeping.py:36-43`. `OutstandingToken` cascades to `BlacklistedToken`, so Django loads the related rows in Python to delete them.
- **Impact:** The first run after a long gap, or after a busy month, holds locks and memory for one large delete.
- **Recommendation:** Delete in primary-key batches with a cap per run.

**Soft-delete patterns**
- `ContactType` uses `deleted_at`, with the queryset method `live()` (`contacts/models.py:32-34, 50`). The **default manager does not filter** soft-deleted rows, so every caller must remember `.live()`. The services do (`contacts/services.py:62, 350`).
- The citizen read filters `type__is_active` only (`contacts/selectors.py:22-23`). A type can be soft-deleted only with no active number (`contacts/services.py:360-371`), so nothing leaks today.
- `Contact` removal sets `is_active=False` (`contacts/services.py:290`), and reads filter it.
- Geography uses `is_active` with `active()`, which includes the ancestors' flags (`geography/models.py:35-44`).
- Overall the pattern is consistent, but it relies on callers rather than on the default manager. That is Info level; it is part of BE-DB-021's test gap.

---

## 12. SQLite vs production engine risks

**BE-DB-021: Engine differences that the SQLite-only suite cannot catch**
- **Severity:** Medium · **Effort:** M
- **Evidence and impact:**

| Difference | Where it matters | Evidence |
|---|---|---|
| **Collation / case.** MySQL `utf8mb4_unicode_ci` compares case- and accent-insensitively; SQLite `=` is case-sensitive and `LIKE` folds ASCII only. | `share_code` (BE-DB-020); `SettingOverride` key mismatch on the dashboard (carries BE-COR-101, `web_admin/api.py:67-72` reads `"review_escalation_hours"` while `runtime()` writes `"REVIEW_ESCALATION_HOURS"`, `posts/views.py:463, 473`); Telugu `icontains` results; `Service name_en__iexact` (`service_requests/services.py:116`) | `settings.py:72`; `setup.sh:212` |
| **Telugu under `utf8mb4_unicode_ci`.** UCA 4.0 rules may treat some combining marks as equal; sort order differs from SQLite's binary order. | Unique `name_key` is built from English only (`geography/models.py:82`), but `name_te` duplicate checks (`geography/services.py:65`) and Telugu ordering (`ordered_for_language`, `geography/models.py:46-47`) can differ | n/a |
| **NULL ordering.** PostgreSQL sorts NULLs first in DESC; MySQL and SQLite sort them last. | `active_block` orders `-blocked_until` to pick "the longest" block, where NULL means indefinite (`citizens/services.py:124-133`): on PostgreSQL the indefinite block wins, on MySQL and SQLite a dated one does, so the reason and end date shown differ by engine | n/a |
| **Locking.** `select_for_update` is ignored on SQLite (`has_select_for_update = False`, `django/db/backends/base/features.py:49`); SQLite also serialises writers (IMMEDIATE). | The contacts locks (`contacts/services.py:63, 350, 533`) and every race above are never exercised; SQLite's single writer hides races that MySQL and PostgreSQL allow | `settings.py:84` |
| **JSONField.** Equality against `{}` and key lookups compile differently on each engine. | `RoleGroup.objects.exclude(requires_setting={})` (`accounts/authz.py:505`); `SettingOverride.value` | n/a |
| **Index length.** utf8mb4 is 4 bytes per character; InnoDB's limit is 3072 bytes per index (DYNAMIC rows). | Largest indexed text is `Device.fcm_token` 255 × 4 = 1020 bytes; composite text indexes are under the limit. **No current risk.** | `notifications/models.py:24` |
| **Transactional DDL.** PostgreSQL and SQLite roll back failed migrations; MySQL does not. | BE-DB-013 | n/a |
| **GROUP BY.** With `sql_mode` narrowed, MySQL accepts what PostgreSQL rejects. | BE-DB-010 | `settings.py:70` |

- **Recommendation:**
  - Once the owner picks the engine, add one CI job that runs the suite on it (a service container), at least for `posts`, `accounts`, `citizens` and `feed`.
  - Add explicit tests for the collation cases above.
  - Use `nulls_last`/`nulls_first` wherever NULL order matters.
  - This continues BE-TEST-101 and BE-CI-001.

### Info findings

**BE-DB-025: The documented stack no longer matches the settings layout and one rule's default**
- **Severity:** Info · **Effort:** S
- **Evidence:**
  - The stack in the brief lists "settings files base, dev, prod, test". The code has one `grama/settings.py`, with the old names kept as aliases (`settings.py:553-557`).
  - `SUPER_ADMIN_CAN_POST` defaults to `True` (`settings.py:406`), while `.claude/project.md` ("Configurable rules") lists `SUPER_ADMIN_CAN_POST=False`.
- **Impact:** Readers of the docs expect separate files and a different posting default for Super Admins.
- **Recommendation:** Update the stack description. Get the owner's decision on the default, then align either the code or `project.md` in all three repos.

**BE-DB-026: Every console request writes the session row**
- **Severity:** Info · **Effort:** n/a
- **Evidence:** `SESSION_SAVE_EVERY_REQUEST = True` (`settings.py:304`), with the database session backend (default).
- **Impact:** One `UPDATE django_session` per console API call. It is needed for the 30-minute idle timeout, and small at Super Admin volume.
- **Recommendation:** Keep it, and count it when sizing. Revisit only if the console grows to many users.

**BE-DB-027: `runtime()` reads `SettingOverride` on every call**
- **Severity:** Info · **Effort:** S
- **Evidence:** `posts/services.py:99-106`. It is called by `app-config/` (`posts/views.py:95`), every citizen post (`citizens/services.py:233-235`), escalation (`posts/services.py:731, 761`) and the settings screen.
- **Impact:** One small indexed query per call. Harmless now, and it adds up under citizen load.
- **Recommendation:** Optional: a short per-process cache, cleared when the settings screen saves.

---

## 13. What is done well

- **First decision wins without locks.**
  - Every status move is `filter(id, status).update(...)`, checked by row count (`posts/services.py:11-14, 346-371, 617-631, 665-668`).
  - Reversals also guard on `decided_at` (`:666`), and escalation on `escalated_at` (`:742-746`).
- **Same-transaction audit and history**, with signals after commit (`posts/services.py:250-281`; `service_requests/services.py:92-101, 216-236`).
- **Authorization is cached and batched.**
  - Two queries load a user's grants once per request (`accounts/authz.py:107-159`).
  - `prime()` batches ancestry (`:188-195`), and `AreaDescriber` loads names per level (`geography/areas.py:151-198`).
- **Tests that keep query counts flat** for the feed, short lists, users and posts (`feed/tests/test_feed.py:191-194`, `feed/tests/test_feed_owner_decisions.py:173-178`, `accounts/tests/test_authz.py:306-332`).
- **Cursor pagination** on the citizen feed and the inbox (`feed/views.py:41-46`; `notifications/views.py:89-92`).
- **Indexes and constraints** match the main filters (section 5). Unique constraints back sibling names, assignments, post targets, translations, tokens and nonces. Check constraints back event dates and "author or citizen" (`posts/models.py:120-131`).
- **Feed media** from correlated subqueries, not prefetches (`feed/views.py:49-70`).
- **Bulk geography import** with batches, inside one transaction (`geography/services.py:187-191`). Migration `posts/0008` batches with `iterator(chunk_size=2000)` and `bulk_create` (`posts/migrations/0008_post_targets.py:7-17`).
- **Atomic counter:** `token_version` is bumped with `F()`, and refresh tokens are blacklisted in bulk (`accounts/tokens.py:74-82`).
- **Lock plus re-check pattern** for the contact-type delete, safe even where locks are no-ops (`contacts/services.py:343-371`).
- **Bounded nearest-village search:** box plus index, `values_list` of three columns, at most 5 rounds to 50 km (`geography/nearest.py:49-85`).
- **Translation retries** through one-off schedules, with no sleeping worker (`posts/tasks.py:49-66`). The outside call sits outside any transaction.
- **utf8mb4 everywhere on MySQL:** client option, test database, server config, database create, dump and restore (`settings.py:69-73`; `setup.sh:206-222`; `backup.sh:29-32`; `restore.sh:95-97`).
- **Consistent backups** with `--single-transaction` for MySQL and custom-format `pg_dump` (`backup.sh:29-38`).
- **No migration drift**, and the whole suite passes (2867 passed).

---

## 14. Not verified

| Item | Reason |
|---|---|
| Real query plans (`EXPLAIN`) for the feed, queue, audit and reports on MySQL or PostgreSQL | No production-engine database available; read-only scope |
| Production `max_connections`, `wait_timeout`, collation, engine version, slow-query log | Production database is set up outside the repo (BE-SEC-003) |
| Table sizes and row counts on qa or production | No database access |
| Number of connections qa's threaded runserver actually holds (BE-DB-011) | Not measured |
| django-q2 process count per cluster (taken as about 5) | From the library's design and code; not measured on a running cluster |
| Whether `posts/0009` collided on MySQL `_ci`, or ran cleanly, on qa or production | No database access |
| Telugu equality and sort behaviour under `utf8mb4_unicode_ci` in practice | Needs a MySQL instance |
| Query counts in production for `notify_users` (tests include the job because `sync=True`) | The measurement environment runs jobs inline |
| The time of day of the housekeeping schedule on each server | Set when `ensure_schedules` first ran; stored only in the database |
| The load test (`deploy/loadtest/locustfile.py`) | Not run; it needs a running server |

---

## 15. Prioritised action list

**High**
1. **BE-DB-001:** rewrite the dashboard and reports as grouped SQL; paginate admin-activity in the database; cache the dashboard briefly.
2. **BE-DB-002:** keyset pagination and a default date window for the audit log; drop or cache the per-page counts; stream the export with only the needed columns, or run it as a job.

**Medium**
3. **BE-DB-005, BE-DB-006, BE-DB-003:** remove the N+1s (citizen summary per page, primed service-request checks, bulk notification fan-out in one job). Add query-count tests with citizen posts and service requests.
4. **BE-DB-008, BE-DB-007:** subquery media count in the feed; add `(status, published_at)`, a Post `created_at` index and a ServiceRequest `updated_at` index; drop the stale Post index after `EXPLAIN`.
5. **BE-DB-004:** serialise Super Admin peer changes and re-check inside the transaction.
6. **BE-DB-014:** lock the parent row or re-count for daily limits and the photo cap; catch unique conflicts and answer 409.
7. **BE-DB-016:** batch escalation per run; queue alerts per post as their own job.
8. **BE-DB-013:** dump the database before `migrate` in both deploy scripts; document recovery from a half-applied migration on MySQL.
9. **BE-DB-021:** one CI job on the chosen engine; collation and NULL-order tests.
10. **BE-DB-018:** owner decision on retention, then prune jobs for notifications, devices, citizen devices and contact messages.
11. **BE-DB-009:** fewer counts per page; prefix or full-text search; throttle anonymous searches.

**Low**
12. **BE-DB-010:** keep the server `sql_mode` defaults and add strict mode.
13. **BE-DB-011:** Gunicorn, or `CONN_MAX_AGE=0`, on qa.
14. **BE-DB-015:** move file writes out of transactions; delete the video file with drafts.
15. **BE-DB-017:** set `poll` to 1–2 s.
16. **BE-DB-019:** batch housekeeping deletes.
17. **BE-DB-020:** case-sensitive `share_code`; retry on conflict.
18. **BE-DB-022:** refresh-token, service-request and schedule race hardening.
19. **BE-DB-023:** make block plus discard atomic, or record the summary first.
20. **BE-DB-024:** reversible, batched data migrations from now on.

**Info**
21. **BE-DB-012:** record the production database settings.
22. **BE-DB-025:** update the documented stack to say "one settings file" (`settings.py:553-557`). Also note that `SUPER_ADMIN_CAN_POST` defaults to **True** in `settings.py:406`, while `.claude/project.md` lists `SUPER_ADMIN_CAN_POST=False`. Settle this with the owner.
23. **BE-DB-026:** `SESSION_SAVE_EVERY_REQUEST` writes the session row on every console request (`settings.py:304`). This is expected for the idle timeout; keep it in mind when sizing.
24. **BE-DB-027:** `runtime()` reads `SettingOverride` uncached on each call (`posts/services.py:99-106`); a short per-process cache is optional.

---

## 16. Production deployment risk

**Migration risk**
- No pending model changes, so today's deploy carries no schema change.
- The recommended index migrations (BE-DB-007) are additive:
  - On PostgreSQL, create them concurrently in a non-atomic migration.
  - On MySQL 8, `ADD INDEX` runs online.
  - Run them outside 02:30 and outside peak hours.
- Dropping the stale Post index is the only destructive schema change proposed; do it in a later release, after `EXPLAIN` confirms nothing uses it.
- Any retention job (BE-DB-018) deletes data. It needs the owner's decision and a backup taken just before its first run.

**Rollback concerns**
- Nine data migrations have no reverse step (BE-DB-024), so rolling back past them means restoring a dump.
- Deploys do not take that dump themselves (BE-DB-013), and the nightly one can be up to 24 hours old.
- On MySQL a failed migration may be half applied, so code rollback alone may not match the schema.
- Practice the restore drill in `deploy/RUNBOOK.md` on a spare server before the first state-wide release.

**Monitoring to add**

| Signal | Why | How (conceptual) |
|---|---|---|
| Slow query log (> 1 s) | Confirms BE-DB-001, 002, 008 and 009 in real use | MySQL `slow_query_log` + `long_query_time`; PostgreSQL `log_min_duration_statement` |
| Connection count vs `max_connections` | Budget in section 2.2; qa runserver (BE-DB-011) | `SHOW STATUS LIKE 'Threads_connected'` / `pg_stat_activity` |
| django-q queue depth and age of the oldest task | Fan-out and escalation backlog (BE-DB-003, 016) | Count of `django_q_ormq` rows and min `lock`; alert above a threshold |
| Failed tasks per hour, plus `push.failed` audit entries | Silent job failures (carries BE-OBS-101) | Count `django_q_task` where `success=false` in the last hour |
| Table sizes for audit, notifications, devices, citizen devices, task | Growth without retention (BE-DB-018) | Weekly size report from `information_schema` / `pg_total_relation_size` |
| Request time for `admin/*`, `audit/*`, `feed/` | Console 504s at nginx's 60 s | Gunicorn access timing or an APM; alert at p95 > 5 s |
| Escalation run time | 120 s job timeout (BE-DB-016) | Log the start and end of `run_escalation` with the count processed |

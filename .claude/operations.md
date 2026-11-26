# Running, testing and deploying the backend

The three repos are expected side by side in one folder, because Django serves the website and console from the web repo's `dist/` and `dist/console/` folders in development.

## Local setup

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_sample_data
python manage.py runserver
```

Linux or macOS: the same with `python3 -m venv .venv` and `source .venv/bin/activate`.

- No `.env` is needed locally; everything defaults to SQLite (`db.sqlite3`). Set `SQLITE_PATH` to use another file.
- `seed_sample_data` creates 2 districts × 2 mandals × 3 villages, one official per role, and posts in every state. It prints each official's username and temporary password once; `--reset-passwords` prints new ones. Safe to run again. On a server (DEBUG off) it and `load_demo_area` refuse unless `--allow-server` is given: qa only, never production.
- The first Super Admin on a real database: `python manage.py create_super_admin --username <name> --name "<full name>"` (a state must be loaded). It prints a temporary password once. A username with spaces goes in quotes: `--username "sita admin"`. Django admin needs a staff account from `createsuperuser`.
- Build the web parts before opening `/` or `/console/`: `npm run build` in BVTGramaConnect, which builds both.
- Android emulator reaches the server at `http://10.0.2.2:8000`. For a real phone, run `runserver 0.0.0.0:8000` and add the PC's LAN address to `ALLOWED_HOSTS`.

## Everyday commands

| Task | Command |
|---|---|
| Tests | `python -m pytest -q -p no:logging` |
| Tests by category, with a report | `python -m grama.testsuite all` (or one category; `--list`, `--check`; `--coverage` adds line coverage per app). Writes `test-reports/BE_TESTS_<scope>_<date>.md`, `.json` and `.html`, and the dashboard `backend-test-dashboard-<date>.html` (health score, coverage, known bugs; git-ignored) |
| Tests by marker | `python -m pytest -q -p no:logging -m rbac` (or `unit`, `integration`, `contract`, `security`, `db`, `concurrency`, `jobs`, `errors`, or a category's key) |
| RBAC matrix (who may call what) | Edit `grama/tests/config/rbac-matrix.json` after the contract; `grama/tests/test_rbac_matrix.py` runs every endpoint × role |
| API schema snapshot, after an agreed API change | `UPDATE_SCHEMA_SNAPSHOT=1 python -m pytest grama/tests/test_schema_snapshot.py`, commit `grama/tests/config/openapi-snapshot.json` with the contract change |
| Every migration to zero and back (slow, about 1 h 45 min) | `RUN_MIGRATION_ROUND_TRIP=1 python -m pytest grama/tests/test_migrations_both_ways.py` |
| Missing migrations | `python manage.py makemigrations --check --dry-run` |
| Telugu messages (after adding or changing a `_()` message) | `python manage.py translations` (updates `locale/te/LC_MESSAGES/django.po`, builds `django.mo`); `--check` only checks. No GNU gettext needed; commit both files |
| Background worker | `python manage.py qcluster` |
| Escalation and housekeeping schedules | `python manage.py ensure_schedules` (once per database; deploy runs it) |
| Role groups and permissions | `python manage.py sync_role_permissions` (also runs after every `migrate`; safe to repeat) |
| Unlock a username | Unlock in the console, or `python manage.py shell -c "from apps.accounts.lockout import unlock; from apps.accounts.models import User; print(unlock(User.objects.get(username='<username>')))"` (axes stores a hash, so `axes_reset_username` does not find it) |
| New sample passwords | `python manage.py seed_sample_data --reset-passwords` |
| Demo content for a real mandal (qa testing on the state's own villages) | `python manage.py load_demo_area --allow-server [--district Prakasam] [--mandal Kandukuru] [--villages 4] [--village <name> …]`: "demo." officials (temporary passwords printed once), posts in every place a citizen looks, numbers with unassignable 0+STD+000 phones. Adds no geography; safe to repeat. Use it instead of `seed_sample_data` on a server with the real state list, which would import its own Guntur/Krishna places and coordinates over it. |
| Import villages | `python manage.py import_geography <file.csv> [--dry-run] [--missing-report out.csv]` |
| Duplicate area names, English or Telugu | `python manage.py geo_duplicates` (read-only; before migrating `geography.0003` it must print "No duplicates.") |
| Census tags in village names ("(Ct)", "(Part)" …) | `python manage.py geo_clean_names` lists; `--apply` renames (audited; census spelling kept in `census_name`) |
| What still uses an area, before switching it off | `python manage.py geo_area_usage DISTRICT 6` (read-only) |
| Villages without coordinates ("Use my location" never finds them) | `python manage.py geo_coordinates [--csv out.csv] [--fail-above 0]` (read-only; the CSV is in import format with empty coordinates) |
| 640 px photo files for media from before `posts.0005` | `python manage.py backfill_medium_images` (`--dry-run` counts only; safe to run again) |
| Translate published posts once `TRANSLATION_PROVIDER` is switched on | `python manage.py translate_published_posts [--since YYYY-MM-DD] [--limit N]` (queues for the worker; safe to run again) |
| Statewide helplines | `python manage.py seed_helplines [--file list.csv]` (safe to repeat; `seed_sample_data` runs it too) |
| Import important numbers | `python manage.py import_contacts <file.csv> [--dry-run]` (all rows or none; columns in `docs/api-contract.md` section 14) |
| Sample file | `apps/geography/sample_data/prototype_villages.csv` |
| Deploy checks | `DEBUG=False python manage.py check --deploy` (needs the production env vars) |

## Environment variables

`.env.example` lists every variable with a comment. All settings are in `grama/settings.py`; `DEBUG=False` switches it to production mode (the qa server runs that way too). Required with `DEBUG=False`: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT. A server without Nginx (the qa server) also sets SERVE_FILES=True, CITIZEN_INTEGRITY_REQUIRED=False for debug builds, and API_DOCS_PUBLIC=True. Also set for production: CSRF_TRUSTED_ORIGINS (the console's session login needs it), FCM_CREDENTIALS_FILE and FCM_PROJECT_ID, PLAY_STORE_URL once the app is published. The platform rules (ADMIN_POSTS_AUTO_PUBLISH and the rest) and the login settings (LOGIN_MAX_FAILURES, LOGIN_LOCKOUT_MINUTES, LOGIN_MAX_FAILURES_PER_USERNAME, LOGIN_USERNAME_WINDOW_MINUTES, LOGIN_IP_MAX_FAILURES, LOGIN_IP_WINDOW_MINUTES, CONSOLE_SESSION_IDLE_MINUTES) can be overridden too, and so can HOUSEKEEPING_KEEP_DAYS (30, how long failed background jobs are kept); keep SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE on.

## Load test

`deploy/loadtest/locustfile.py` is the realistic mix: citizens (village picker, feed, scrolling, post detail, Contacts), a staging official (login, refresh, notifications, post list, a draft written and deleted again) and failed logins. Run it through the runner, against **staging only** (BVTGC-38), with Gunicorn behind Nginx:

```
cp .env.test.example .env.test        # git-ignored: LOADTEST_BASE_URL, optionally a staging official and a metrics address
python -m grama.perftest load --check # checks the config and the target, runs nothing
python -m grama.perftest load         # 1, 5, 10, 25, 50 users in turn
python -m grama.perftest stress       # more users each round until the error rate or P95 crosses the limit
python -m grama.perftest spike        # 0 to 50 users at once, and the recovery time
python -m grama.perftest soak         # 10 minutes sustained
```

Profiles, the SLA and the refused hosts (production and qa) are in `deploy/loadtest/profiles.json`. Each run writes `test-reports/<kind>-report-<date>.json` and `.html`; the test dashboard shows the newest. `--smoke` is a 20-second trial of the runner itself (2 users), for a laptop server.

## Deploying

Everything for the Ubuntu server is in `deploy/`: start with `deploy/README.md`, then `deploy/RUNBOOK.md` for day-to-day work, the first Super Admin, backups, secrets and push, and `deploy/DATA_MOVE.md` for moving master data from a laptop.

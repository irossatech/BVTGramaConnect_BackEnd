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
- `seed_sample_data` creates 2 districts × 2 mandals × 3 villages, one official per role, and posts in every state. It prints each official's username and temporary password once; `--reset-passwords` prints new ones. Safe to run again.
- The first Super Admin on a real database: `python manage.py create_super_admin --username <name> --name "<full name>"` (a state must be loaded). It prints a temporary password once. Django admin needs a staff account from `createsuperuser`.
- Build the web parts before opening `/` or `/console/`: `npm run build` in BVTGramaConnect, which builds both.
- Android emulator reaches the server at `http://10.0.2.2:8000`. For a real phone, run `runserver 0.0.0.0:8000` and add the PC's LAN address to `ALLOWED_HOSTS`.

## Everyday commands

| Task | Command |
|---|---|
| Tests | `python -m pytest -q -p no:logging` |
| Missing migrations | `python manage.py makemigrations --check --dry-run` |
| Background worker | `python manage.py qcluster` |
| Escalation and housekeeping schedules | `python manage.py ensure_schedules` (once per database; deploy runs it) |
| Role groups and permissions | `python manage.py sync_role_permissions` (also runs after every `migrate`; safe to repeat) |
| Unlock a username | `python manage.py axes_reset_username <username>`, or Unlock in the console |
| New sample passwords | `python manage.py seed_sample_data --reset-passwords` |
| Import villages | `python manage.py import_geography <file.csv> [--dry-run] [--missing-report out.csv]` |
| Duplicate area names (before migrating `geography.0003`) | `python manage.py geo_duplicates` (read-only; must print "No duplicates.") |
| Statewide helplines | `python manage.py seed_helplines [--file list.csv]` (safe to repeat; `seed_sample_data` runs it too) |
| Import important numbers | `python manage.py import_contacts <file.csv> [--dry-run]` (all rows or none; columns in `docs/api-contract.md` section 14) |
| Sample file | `apps/geography/sample_data/prototype_villages.csv` |
| Deploy checks | `DJANGO_SETTINGS_MODULE=grama.settings.prod python manage.py check --deploy` (needs the prod env vars) |

## Environment variables

`.env.example` lists every variable with a comment. Required in production: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT. Also set for production: CSRF_TRUSTED_ORIGINS (the console's session login needs it), FCM_CREDENTIALS_FILE and FCM_PROJECT_ID, PLAY_STORE_URL and APP_STORE_URL once the apps are published. The platform rules (ADMIN_POSTS_AUTO_PUBLISH and the rest) and the login settings (LOGIN_MAX_FAILURES, LOGIN_LOCKOUT_MINUTES, CONSOLE_SESSION_IDLE_MINUTES) can be overridden too, and so can HOUSEKEEPING_KEEP_DAYS (30, how long failed background jobs are kept); keep SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE on.

## Load test

`deploy/loadtest/locustfile.py` exercises the citizen feed and the login endpoint. Install with `pip install -r requirements.txt`, load sample data, then:

```
locust -f deploy/loadtest/locustfile.py --host http://127.0.0.1:8000 --users [EXPECTED USERS] --spawn-rate 20 --run-time 5m --headless --csv deploy/loadtest/results/run
```

Run it against the production-sized server with Gunicorn, not `runserver`, for real numbers.

## Deploying

Everything for the Ubuntu server is in `deploy/`: start with `deploy/README.md`, then `deploy/RUNBOOK.md` for day-to-day work, the first Super Admin, backups, secrets and push, and `deploy/DATA_MOVE.md` for moving master data from a laptop.

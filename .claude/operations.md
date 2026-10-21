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
- `seed_sample_data` creates 2 districts × 2 mandals × 3 villages, one official per role, and posts in every state. It prints each official's username and temporary password. Safe to run again.
- To give an account the platform Super Admin role: `createsuperuser`, then in Django admin add a role assignment (role SUPER_ADMIN, level STATE, the state's id).
- Invite and reset emails print in the `runserver` or `qcluster` terminal in development.
- Build the web parts before opening `/` or `/console/`: `npm run build` in BVTGramaConnect, which builds both.
- Android emulator reaches the server at `http://10.0.2.2:8000`. For a real phone, run `runserver 0.0.0.0:8000` and add the PC's LAN address to `ALLOWED_HOSTS`.

## Everyday commands

| Task | Command |
|---|---|
| Tests | `python -m pytest -q -p no:logging` |
| Missing migrations | `python manage.py makemigrations --check --dry-run` |
| Background worker | `python manage.py qcluster` |
| Escalation schedule | `python manage.py ensure_schedules` (once per database; deploy runs it) |
| Import villages | `python manage.py import_geography <file.csv> [--dry-run] [--missing-report out.csv]` |
| Sample file | `apps/geography/sample_data/prototype_villages.csv` |
| Deploy checks | `DJANGO_SETTINGS_MODULE=grama.settings.prod python manage.py check --deploy` (needs the prod env vars) |

## Environment variables

`.env.example` lists every variable with a comment. Required in production: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT. Also set for production: CSRF_TRUSTED_ORIGINS, the EMAIL_* variables (SMTP), CONSOLE_LOGIN_URL, FCM_CREDENTIALS_FILE and FCM_PROJECT_ID, PLAY_STORE_URL and APP_STORE_URL once the apps are published. The platform rules (ADMIN_POSTS_AUTO_PUBLISH and the rest) can be overridden too.

## Load test

`deploy/loadtest/locustfile.py` exercises the citizen feed and the login endpoint. Install with `pip install -r requirements.txt`, load sample data, then:

```
locust -f deploy/loadtest/locustfile.py --host http://127.0.0.1:8000 --users [EXPECTED USERS] --spawn-rate 20 --run-time 5m --headless --csv deploy/loadtest/results/run
```

Run it against the production-sized server with Gunicorn, not `runserver`, for real numbers.

## Deploying

Everything for the Ubuntu server is in `deploy/`: start with `deploy/README.md`, then `deploy/RUNBOOK.md` for day-to-day work, backups, secrets, email and push, and `deploy/DATA_MOVE.md` for moving master data from a laptop.

# BVTGramaConnect_BackEnd

Django backend for BVT Grama Connect, the village news platform for Andhra Pradesh. It serves the REST API for the mobile app and the Super Admin console, and in development it also serves the two ported web parts.

- Project brief: `CLAUDE.md` in the folder above this repo.
- What the prototypes contain and how they are ported: [docs/prototype-inventory.md](docs/prototype-inventory.md).
- Every endpoint, object and permission rule: [docs/api-contract.md](docs/api-contract.md).

## Requirements

- Python 3.12 or 3.13.
- Nothing else for local development. The database is SQLite and background jobs use the same database, so there is no MySQL, Redis or other service to install.

The three repos are expected side by side, because Django serves the website and console from the web repo's build:

```
Repo/
  BVTGramaConnect_BackEnd/   this repo
  BVTGramaConnect/           public website (served at /) and Super Admin console (served at /console/)
  BVTGramaConnect_Mobile/    Flutter app
```

## Run it locally

### Windows (PowerShell)

```powershell
cd BVTGramaConnect_BackEnd
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

If PowerShell refuses to run `Activate.ps1`, allow local scripts once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. You can also skip activation and call `.\.venv\Scripts\python.exe` directly.

### Linux or macOS

```bash
cd BVTGramaConnect_BackEnd
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### What each step does

1. **Virtual environment:** keeps this project's packages apart from the rest of the machine.
2. **`pip install`:** installs the pinned packages in `requirements.txt`.
3. **`migrate`:** creates `db.sqlite3` in the repo folder with every table.
4. **`createsuperuser`:** asks for a username, an email, a name and a password. This account opens Django admin. To make it the platform's Super Admin as well, add a role assignment in Django admin (role `SUPER_ADMIN`, level `STATE`, the state's id); it can then log in to `/console/` with the same username and password.
5. **`runserver`:** starts the development server at http://127.0.0.1:8000/.

A `.env` file is optional for local work. To change a setting, copy `.env.example` to `.env` and edit it.

### Logins

Officials log in with a username (or their email) and a password; there is no SMS. An admin invites someone from the console or the app, and they get an email with their username and their role's default password, which they must change at first login. The Super Admin sets each role's default password in the console. In development the email is printed in the `runserver` or `qcluster` terminal. `python manage.py seed_sample_data` creates one official per role and prints their usernames and temporary passwords.

### Background jobs

Invite and reset emails, push notifications and review escalation run as django-q2 jobs. The queue is stored in the database. To process jobs, start the worker in a second terminal, with the virtual environment active:

```
python manage.py qcluster
```

The API keeps working without the worker; queued jobs wait until it starts. Stop it with Ctrl+C.

Register the recurring jobs once, and again after each deploy (safe to repeat):

```
python manage.py ensure_schedules
```

This creates the review-escalation job, which runs every 15 minutes and alerts the next level up about posts pending longer than `REVIEW_ESCALATION_HOURS`.

### URLs

| URL | What |
|---|---|
| http://127.0.0.1:8000/api/v1/health/ | Health check: `{"status": "ok"}` |
| http://127.0.0.1:8000/api/v1/ | REST API |
| http://127.0.0.1:8000/api/schema/ | OpenAPI schema |
| http://127.0.0.1:8000/api/docs/ | Swagger UI for the schema |
| http://127.0.0.1:8000/django-admin/ | Django admin, developers only |
| http://127.0.0.1:8000/console/ | Super Admin console from `../BVTGramaConnect/dist/console` |
| http://127.0.0.1:8000/ | Public website from `../BVTGramaConnect/dist` |

Until a web part is ported, its URL shows a "not ported yet" page naming the folder Django looked in. Change the folders with `WEB_PUBLIC_DIR` and `WEB_ADMIN_DIR` in `.env`.

### Testing the mobile app against this server

- **Android emulator:** use `http://10.0.2.2:8000`. It is already in `ALLOWED_HOSTS`.
- **Real phone on the same Wi-Fi:** run `python manage.py runserver 0.0.0.0:8000` and add the PC's LAN address to `ALLOWED_HOSTS` in `.env`, for example `ALLOWED_HOSTS=localhost,127.0.0.1,10.0.2.2,192.168.1.20`.

## Tests

```
python -m pytest
```

Tests run against a fresh in-memory SQLite database with `grama.settings.test`. Background jobs run inline, so no worker is needed.

## Project layout

```
apps/                 every Django app (Python package "apps"; each keeps its short label)
  accounts/           officials, password login, invites, role assignments, users API
  geography/          State, District, Mandal, Village; nearest village; CSV import
  posts/              posts, photos, review, categories, runtime settings, escalation
  feed/               public citizen feed
  notifications/      devices, push, officials' inbox, push texts
  audit/              audit log and CSV export
  web_public/         serves the public website; site-config and contact API
  web_admin/          serves the Super Admin console; dashboard and reports API
grama/                Django project
  settings/           base.py, dev.py (default for manage.py), prod.py (wsgi/asgi), test.py
  urls.py             URL layout
  sitefiles.py        serves the web parts while DEBUG is on
  tests/              project-level tests
deploy/               server setup, deploy, backup and restore; loadtest/ holds the Locust test
docs/                 API contract, prototype inventory, UAT checklist, security review
conftest.py           shared test fixtures (map and one official per role)
manage.py
pytest.ini
requirements.txt      the one requirements file for every environment
```

Apps are imported as `apps.<name>` (for example `from apps.posts.services import review`). Their labels stay short (`accounts`, `posts`, …), so table names, migrations and `AUTH_USER_MODEL = "accounts.User"` are unchanged.

## Settings

`manage.py` uses `grama.settings.dev` unless `DJANGO_SETTINGS_MODULE` says otherwise. `wsgi.py` and `asgi.py` default to `grama.settings.prod`.

On the server, `grama.settings.prod` needs these environment variables and refuses to start without them:

| Variable | Example |
|---|---|
| `SECRET_KEY` | a long random string |
| `ALLOWED_HOSTS` | `gramaconnect.example.in` |
| `DATABASE_URL` | `mysql://grama:password@127.0.0.1:3306/grama` |
| `MEDIA_ROOT` | `/var/media` |
| `STATIC_ROOT` | `/srv/gramaconnect/static` |

- **Database:** any engine Django supports, chosen by `DATABASE_URL`. For MySQL the settings add `utf8mb4` and strict mode automatically. Create the database with `CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci` so Telugu is stored correctly.
- **Platform rules** from CLAUDE.md can be overridden in env: `ADMIN_POSTS_AUTO_PUBLISH`, `REVIEW_FIRST_DECISION_WINS`, `REVIEW_ESCALATION_HOURS`, `SUPER_ADMIN_CAN_POST`.
- `.env.example` lists every variable.

## On the server

Install the same `requirements.txt` as everywhere else; it includes Gunicorn and the drivers for both MySQL and PostgreSQL. On Linux, mysqlclient builds from source, so install `pkg-config default-libmysqlclient-dev build-essential python3-dev` first (`deploy/setup.sh` does this). Then, with `DJANGO_SETTINGS_MODULE=grama.settings.prod` and the variables above set:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn grama.wsgi:application --bind 127.0.0.1:8001 --workers 3
python manage.py qcluster
```

Nginx serves the public website at `/`, the console at `/console/`, `/static/` and `/media/`, and proxies `/api/` and `/django-admin/` to Gunicorn. Django does not serve the web parts outside development.

## Deployment

[deploy/README.md](deploy/README.md) sets up an Ubuntu LTS server with one command (`sudo bash deploy/setup.sh config.env`): hardening, MySQL or PostgreSQL, systemd services, Nginx with Let's Encrypt, and nightly encrypted backups. Releases go out with `deploy/deploy.sh`. Operations are in [deploy/RUNBOOK.md](deploy/RUNBOOK.md); moving seed data from SQLite is in [deploy/DATA_MOVE.md](deploy/DATA_MOVE.md).

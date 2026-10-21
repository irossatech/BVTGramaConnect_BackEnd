# Backend architecture

Django 5.2 + Django REST Framework. One project package, `grama`, and eight apps inside the `apps/` package. Apps are imported as `apps.<name>`; each keeps its short label (`accounts`, `posts`, …), so tables, migrations, URL namespaces and `AUTH_USER_MODEL = "accounts.User"` use the short name.

Top level of the repo: `apps/`, `grama/` (settings, URLs, shared helpers, project tests in `grama/tests/`), `deploy/` (server scripts; `deploy/loadtest/` for Locust), `docs/`, plus `manage.py`, `conftest.py`, `pytest.ini` and the single `requirements.txt` used in every environment.

## Apps and what each owns

| App | Owns |
|---|---|
| `accounts` | User, RoleAssignment, RoleDefaultPassword, LoginAttempt. Login, refresh, logout, password change, `/me`, users and role-assignment API, default passwords per role. Invite and reset emails (`tasks.py`). Area scope helpers (`scope.py`). JWT with a token version (`tokens.py`, `authentication.py`). `seed_sample_data` command. |
| `geography` | State, District, Mandal, Village. Public cascading lists, nearest village (`nearest.py`), Super Admin writes and tree (`views_admin.py`), CSV import (`importer.py`, `import_geography` command), area references and containment (`areas.py`). |
| `posts` | Category, Post (with optional event date and the urgent flag), PostMedia (photo or video), ReviewAction, SettingOverride. Post API, review queue, stats, runtime settings, "Visible to" choices (`posting_targets`). Photo and video processing (`media.py`; ffprobe and ffmpeg when installed, else the phone sends the length and a poster). Escalation job (`tasks.py`, `ensure_schedules` command). |
| `audit` | AuditLog. Audit list and CSV export. |
| `feed` | Public citizen feed with news search and a video filter, "Don't miss" (upcoming events) and the urgent list. No models of its own; reads Post. |
| `notifications` | Device, Notification, NotificationTemplate. Push gateways (`push.py`), signal receivers (`receivers.py`), inbox and push logic (`services.py`), delivery jobs (`tasks.py`). |
| `web_public` | ContactMessage. Site-config (with help lines and the website link), showcase and contact API, and the Super Admin's contact-messages API for the console. Serves the website files while DEBUG is on. |
| `web_admin` | Console-only dashboard and reports API (`api.py`). Serves the console files while DEBUG is on. |
| `grama` | Project package: settings, URLs, error shape (`exceptions.py`), pagination, retry with backoff (`retry.py`), client IP (`http.py`), web file serving (`sitefiles.py`), health check. |

## Rules that live in exactly one place

- **Post status changes** happen only in `apps/posts/services.py`. Each move is one conditional UPDATE (`filter(id=..., status=...).update(...)`), so the first decision wins on any database. It writes a ReviewAction and an audit entry in the same transaction, then sends a signal after commit.
- **Area containment** lives in `apps/geography/areas.py` (`AreaRef`, `area_is_within`, `within_q`). Role reach lives in `apps/accounts/scope.py` (`user_areas`, `is_within`, `can_manage`, `manageable_users`, `capabilities`). Never re-implement either in a view.
- **Audit entries** are written only through `apps.audit.services.record(...)`, inside the transaction of the change.
- **Notifications** react to `apps/posts/signals.py` (`post_published`, `post_submitted`, `post_decided`, `post_escalated`). `posts` never imports `notifications`.
- **Passwords**: defaults, invites, resets, login checks and throttling are in `apps/accounts/credentials.py`.
- **Sessions**: `apps/accounts/tokens.py` issues JWTs carrying the user's `token_version` ("tv" claim). `apps/accounts/authentication.py` checks it on every request and blocks everything except `/me`, password change, refresh and logout while `must_change_password` is set.
- **Runtime settings**: `apps.posts.services.runtime(name)` reads a SettingOverride row first, then the Django setting.
- **Urgent** is set at create or edit by admins, or later by a reviewer through `apps.posts.services.set_urgent`, which audits it. Urgent posts push at high priority; others at normal.
- **"Visible to"**: what an author may target is checked in `posting_assignment`; the choices the app shows come from `posting_targets`. Both live in `apps/posts/services.py`.

## Settings

| Module | Used by | Notes |
|---|---|---|
| `grama/settings/base.py` | everything | Apps, DRF, JWT, django-q2, platform rules, email, push, limits. Reads env and an optional `.env`. |
| `grama/settings/dev.py` | `manage.py` (default) | SQLite (`SQLITE_PATH` overrides the file), DEBUG on, console email. |
| `grama/settings/prod.py` | `wsgi.py`, `asgi.py` (default) | Requires SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT. MySQL gets utf8mb4 automatically. |
| `grama/settings/test.py` | pytest | Dev plus in-memory SQLite, synchronous jobs, locmem email and push. |

## URL layout

| Path | Served by |
|---|---|
| `/api/v1/` | REST API, every app's `urls.py` plus `apps/web_public/api_urls.py` and `apps/web_admin/api_urls.py` |
| `/api/schema/`, `/api/docs/` | OpenAPI schema and Swagger UI |
| `/django-admin/` | Django admin, developers only. In production Nginx allows only `OFFICE_IPS` from the deploy config, and nobody when it is empty. |
| `/console/` | Console files from `WEB_ADMIN_DIR` (default `../BVTGramaConnect/dist/console`), DEBUG only |
| `/media/` | Uploaded files, DEBUG only |
| `/` | Website files from `WEB_PUBLIC_DIR` (default `../BVTGramaConnect/dist`), DEBUG only; must stay last |

In production Nginx serves `/`, `/console/`, `/static/` and `/media/` directly; `grama/sitefiles.py` answers 404 when DEBUG is off.

## Background jobs

django-q2 with the Django ORM as broker (no Redis). Start the worker with `python manage.py qcluster`.
- Invite and reset emails: `apps.accounts.tasks.send_credentials_email`.
- Push to topics and to officials' devices: `apps.notifications.tasks`. Topic pushes for urgent posts go at high priority, the rest at normal.
- Escalation of overdue pending posts: a schedule created by `python manage.py ensure_schedules`, every ESCALATION_CHECK_MINUTES.
- Email and push retry with backoff (`grama/retry.py`, DELIVERY_RETRY_*). A final failure is audited as `email.failed` or `push.failed`.

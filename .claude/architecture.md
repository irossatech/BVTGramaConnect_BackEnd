# Backend architecture

Django 5.2 + Django REST Framework. One project package, `grama`, and twelve apps inside the `apps/` package. Apps are imported as `apps.<name>`; each keeps its short label (`accounts`, `posts`, …), so tables, migrations, URL namespaces and `AUTH_USER_MODEL = "accounts.User"` use the short name.

Top level of the repo: `apps/`, `grama/` (settings, URLs, shared helpers, project tests in `grama/tests/`), `deploy/` (server scripts; `deploy/loadtest/` for Locust), `docs/`, plus `manage.py`, `conftest.py`, `pytest.ini` and the single `requirements.txt` used in every environment.

## Apps and what each owns

| App | Owns |
|---|---|
| `accounts` | User, RoleGroup (each group's key, level and rank), RoleAssignment (user, group, area). App login with JWT and a token version (`tokens.py`, `authentication.py`), console session login, password change, `/me`, users and role-assignment API (`views_auth.py`, `views_users.py`). Lockout with django-axes (`lockout.py`). The role matrix (`role_matrix.py`), its sync (`sync.py`, run after every `migrate` and by `sync_role_permissions`) and the permission checks (`authz.py`, `permissions.py`). `seed_sample_data` and `create_super_admin` commands. |
| `geography` | State, District, Mandal, Village (sibling names unique per parent in the database). Public cascading lists, nearest village (`nearest.py`), Super Admin writes and tree (`views_admin.py`: one class per URL for the public read and the write), the only writer of areas (`services.py`) with soft data-quality warnings (`validation.py`), CSV import (`importer.py`, `import_geography` command, through `services.py`), `geo_duplicates` check, `geo_coordinates` (villages "Use my location" cannot find), area references and containment (`areas.py`). |
| `posts` | Category, Post (with optional event date and the urgent flag), PostMedia (photo or video), ReviewAction, SettingOverride. Post API, review queue, stats, runtime settings, "Visible to" choices (`posting_targets`). Photo and video processing (`media.py`: 1280, 640 and 320 px WebP; ffprobe and ffmpeg when installed, else the phone sends the length and a poster; `backfill_medium_images` for older media). Escalation job (`tasks.py`, `ensure_schedules` command). |
| `audit` | AuditLog. Audit list and CSV export. |
| `feed` | Public citizen feed with news search and a video filter, "Don't miss" (upcoming events) and the urgent list. No models of its own; reads Post. |
| `notifications` | Device, Notification, NotificationTemplate. Push gateways (`push.py`), signal receivers (`receivers.py`), inbox and push logic (`services.py`), delivery jobs (`tasks.py`). |
| `web_public` | ContactMessage. Site-config (with help lines and the website link), showcase and contact API, and the Super Admin's contact-messages API for the console. Serves the website files while DEBUG is on. |
| `web_admin` | Console-only dashboard and reports API (`api.py`). Serves the console files while DEBUG is on. |
| `contacts` | ContactType, Contact: the app's important numbers (area numbers and statewide helplines). Phone grammar (`phones.py`), the only writer (`services.py`: create, update, soft remove, CSV import, types), read queries (`selectors.py`), citizen and official views (`views_public.py`, `views_admin.py`), `seed_helplines` and `import_contacts` commands. `web_public` site-config reads its statewide helplines. |
| `citizens` | CitizenDevice: a citizen's phone with its posting token (stored as a hash). Citizens never sign in: the token is issued after a Google Play Integrity check (`integrity.py`: signed one-time nonces, the verdict). Registration, the phone's village, push token and language, posting with the daily limit (a runtime setting), and the Super Admin's temporary block, unblock and discard-all (`services.py`). One phone's installs are linked by a hashed Android ID (`same_phone`, `active_block`), so limits and blocks survive a reinstall. The citizen API under `citizen/` (`views.py`). Citizen posts themselves are made by `posts.services.create_citizen_post`; the decision push to the phone is `notifications.services.tell_citizen`. |
| `service_requests` | Service (the list the Super Admin manages, with an optional uploaded logo re-encoded as WebP, else a drawn icon; key never changes, never deleted), ServiceRequest (a citizen's request for the phone's village, with address) and ServiceRequestEvent (every change). Public `services/`, citizen create and list with the posting token, officials' list, read and change, the Super Admin's `admin/services/` (`views.py`). Reads in `selectors.py` (`visible_requests`, `village_admin_of`); the only writer in `services.py` (services, `create_request` with the automatic assignment to the Village Admin, `update_request`), with history, audit and the notifications (templates `service_request_new`, `service_request_assigned_you`, `service_request_updated`, sent by `notifications.services`). Migrations `0001`–`0002` are the removed first version; the current models start at `0003`. |
| `ads` | Ad: a Home banner for one area (STATE is every village), with dates and an on/off switch. The citizen's `ads/` list and the Super Admin's `admin/ads/` (`views.py`). The only writer, with the image check and WebP re-encode, area and date checks, audit, and the citizen query `for_village` (`services.py`). |
| `grama` | Project package: settings, URLs, error shape (`exceptions.py`), pagination, retry with backoff (`retry.py`), client IP (`http.py`), web file serving (`sitefiles.py`), health check. |

## Rules that live in exactly one place

- **Post status changes** happen only in `apps/posts/services.py`. Each move is one conditional UPDATE (`filter(id=..., status=...).update(...)`), so the first decision wins on any database. It writes a ReviewAction and an audit entry in the same transaction, then sends a signal after commit. Citizen posts too: `create_citizen_post` puts them straight into PENDING; they have no `author`, a `citizen` device instead, `posted_as_role` `CITIZEN`, and are never sent for recheck.
- **Area containment** lives in `apps/geography/areas.py` (`AreaRef`, `area_is_within`, `within_q`). Who may do what, and where, lives in `apps/accounts/authz.py` (`can`, `areas_for`, `area_q`, `can_act_on`, `can_assign`, `manageable_users`) and the DRF class `HasPerm` in `apps/accounts/permissions.py`. Never re-implement either in a view, and never test a group's name or key.
- **Role matrix**: which group holds which permission, and each group's level and rank, is written only in `apps/accounts/role_matrix.py`. `sync_role_permissions`, also run after `migrate`, makes the database match it.
- **Audit entries** are written only through `apps.audit.services.record(...)`, inside the transaction of the change.
- **Notifications** react to `apps/posts/signals.py` (`post_published`, `post_submitted`, `post_decided`, `post_escalated`). `posts` never imports `notifications`.
- **Passwords**: login checks, temporary passwords (set or generated, shown once) and new-password checks are in `apps/accounts/credentials.py`; lockout and unlock in `apps/accounts/lockout.py`. A reset also clears a lockout.
- **Sessions**: `apps/accounts/tokens.py` issues JWTs carrying the user's `token_version` ("tv" claim); console sessions store the same version at login. `apps/accounts/authentication.py` checks it on every request for tokens and sessions, enforces CSRF on session writes, and blocks everything except `/me`, password change, the logouts and refresh while `must_change_password` is set.
- **Runtime settings**: `apps.posts.services.runtime(name)` reads a SettingOverride row first, then the Django setting.
- **Urgent** is set at create or edit by admins, or later by a reviewer through `apps.posts.services.set_urgent`, which audits it. Urgent posts push at high priority; others at normal.
- **"Visible to"**: what an author may target is checked in `posting_assignment`; the choices the app shows come from `posting_targets`. Both live in `apps/posts/services.py`. The area picker itself is `authz.area_targets(user, perm)`, shared with the important numbers' "Who sees it".
- **Districts, mandals and villages** are written only by `apps/geography/services.py` (`create_area`, `update_area`), for the API and the CSV import alike: parent check, unique names (the database constraint is the real guard), audit with old and new values, and `warnings`. The import writes its villages through `import_villages`: the same checks and warnings, in bulk, so a state's 18,000 villages take seconds.
- **Important numbers** are written only by `apps/contacts/services.py`: permission over the area, validation, write and audit (phones masked) in one transaction.

## Settings

| Module | Used by | Notes |
|---|---|---|
| `grama/settings/base.py` | everything | Apps, DRF, JWT, sessions and CSRF, django-axes lockout, Argon2 and the password validators, django-q2, platform rules, push, limits. Reads env and an optional `.env`. |
| `grama/settings/dev.py` | `manage.py` (default) | SQLite (`SQLITE_PATH` overrides the file), DEBUG on, session and CSRF cookies allowed over plain HTTP. |
| `grama/settings/prod.py` | `wsgi.py`, `asgi.py` (default) | Requires SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT. MySQL gets utf8mb4 automatically. |
| `grama/settings/test.py` | pytest | Dev plus in-memory SQLite, MD5 hashing for speed, synchronous jobs, locmem push. |

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
- Push to topics and to officials' devices: `apps.notifications.tasks`. Topic pushes for urgent posts go at high priority, the rest at normal.
- Escalation of overdue pending posts: a schedule created by `python manage.py ensure_schedules`, every ESCALATION_CHECK_MINUTES.
- Daily housekeeping (`grama/housekeeping.py`, the same command registers it): deletes failed django-q tasks older than HOUSEKEEPING_KEEP_DAYS, expired JWT refresh tokens and expired sessions. django-q2 caps successful tasks itself (`save_limit`) and django-axes removes expired login attempts itself. The audit log is never cleaned.
- Push retries with backoff (`grama/retry.py`, DELIVERY_RETRY_*). A final failure is audited as `push.failed`.

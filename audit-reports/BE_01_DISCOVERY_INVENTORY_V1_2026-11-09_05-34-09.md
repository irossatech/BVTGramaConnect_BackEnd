# BACKEND DISCOVERY AND INVENTORY

| Item | Value |
|---|---|
| Report | BE_01 Discovery and Inventory |
| Version | V1 (first run; `audit-reports/` did not exist) |
| Date and time (IST) | 2026-11-09 05:34:09 |
| Repository | `C:\BVTGramaConnect_BackEnd` |
| Branch audited | `docs/single-settings-file` |
| Commit audited | `db64d8417bbf4b47b7f0e9d72c9cf33f9ce50735` (2026-11-07, "Docs and deploy scripts for the single settings file"; 152 commits in history) |
| Auditor | Claude (AI-assisted audit) |
| Stage | 1 of 3 (Discovery and Inventory) |
| Mode | Read-only. No source, config, migration or asset file was changed. This report is the only file created. |
| Earlier reports used as input | None (first run). Repo documents read as context: `CLAUDE.md`, `.claude/*.md`, `docs/security-review.md`, `docs/api-contract.md` (endpoint cross-check), `deploy/**/README.md`, `RUNBOOK.md`. |

**Scope covered:** the whole BackEnd repo: `grama/`, all 14 apps in `apps/`, migrations (counted, not reviewed line by line), `deploy/` (root-server path and the panel path in `deploy/qa/`), `.github/`, tests and fixtures, `requirements.txt`, and docs vs code.

**Scope NOT covered:**
- The web repo (`C:\BVTGramaConnect`) and the mobile repo. I only used the web repo's location to understand how files are served.
- The real servers: the qa server, production at www.gramaconnect.com, and the panel nginx, which is not in the repo.
- Runtime behaviour under load, and anything that needs a running MySQL or PostgreSQL.
- A line-by-line security review of each migration.

**Commands run (all read-only):**

| Command | Result |
|---|---|
| `git rev-parse`, `git log`, `git ls-files`, `wc -l` | Line counts |
| `pip list` (local venv) | Installed versions |
| `manage.py check` | 0 issues |
| `manage.py check --deploy`, with `DEBUG=False` and throwaway env values pointing at the scratchpad | 21 drf-spectacular warnings, 0 security warnings, 3 silenced |
| `manage.py makemigrations --check --dry-run` | "No changes detected" |
| `manage.py showmigrations` (local dev SQLite) | 4 unapplied |
| `manage.py spectacular --validate --format openapi-json`, written to a temp file | 128 operations, 100 paths, 30 warnings (17 unique), 28 errors (4 unique) |
| `pytest --collect-only -q` | 2,869 tests |
| URL resolver walk (`get_resolver()`) | 296 URL patterns |

**Not run:**
- pip-audit, Bandit and Semgrep. They are not installed locally, and installing them was out of bounds. CI runs them; see section 10.

Severity counts: **Critical 0 · High 2 · Medium 14 · Low 21 · Info 5** (42 findings).
**Overall score: 6.5 / 10.**

---

## 1. Repo overview and stack verification

**Layout.** At the top level there are `apps/` (14 apps), `grama/` (the project package and `grama/tests/`), `deploy/`, `docs/`, `.github/`, `manage.py`, `conftest.py`, `pytest.ini` and `requirements.txt`. The `.gitignore` covers `.env`, `db.sqlite3*`, `media/`, `.test-media/`, `staticfiles/` and the G16 test-run artefacts.

**Size** (from `git ls-files '*.py' | xargs wc -l`): 339 Python files and 34,528 lines.

| App | Source lines (without tests and migrations) | Test lines | Migrations |
|---|---|---|---|
| accounts | 4,182 | 3,107 | 9 |
| posts | 2,789 | 1,944 | 9 |
| geography | 2,370 | 996 | 4 |
| contacts | 1,362 | 975 | 3 |
| notifications | 1,254 | 759 | 12 |
| service_requests | 1,020 | 796 | 6 |
| citizens | 922 | 948 | 2 |
| web_admin | 613 | 478 | 1 |
| ads | 501 | 554 | 2 |
| feed | 406 | 466 | 0 |
| web_public | 390 | 281 | 3 |
| audit | 352 | 95 | 2 |
| bills | 333 | 231 | 3 |
| share | 268 | 467 | 0 |

### Stack: documented vs actual

| Component | Documented (brief) | `requirements.txt` | Local `.venv` (`pip list`) | Verdict |
|---|---|---|---|---|
| Python | 3.12 / 3.13 | comment says 3.12 or 3.13 (`requirements.txt:2`) | 3.13.15 | OK locally. CI uses 3.12 (`security.yml`). `setup.sh` claims Ubuntu 22.04 support, but 22.04 ships 3.10 (BE-OPS-003). |
| Django | 5.2.18 | 5.2.18 | 5.2.18 | OK |
| DRF | 3.18.3 | 3.18.3 | 3.18.3 | OK |
| simplejwt | 5.5.1 | 5.5.1 | 5.5.1 | OK |
| drf-spectacular | 0.30 | 0.30.0 | 0.30.0 | OK |
| django-q2 | 1.11.1 | 1.11.1 | 1.11.1 | OK |
| django-environ | yes | 0.14.0 | 0.14.0 | OK |
| **Settings files** | **"base, dev, prod, test"** | — | — | **Mismatch.** There is one `grama/settings.py`, and `DEBUG` picks the mode. `base/dev/prod/test` are only `sys.modules` aliases (`grama/settings.py:554-557`). See BE-DOC-001. |
| Pillow | 13 | 13.0.0 | 13.0.0 | OK |
| django-axes, argon2-cffi | 8.3.1, yes | 8.3.1, 25.1.0 | 8.3.1, 25.1.0 | OK |
| google-auth, requests | yes | 2.62.0, 2.34.2 | same | OK |
| Gunicorn, mysqlclient, psycopg 3 | yes | 26.2.0, 2.3.0, 3.3.6 | same | OK |
| pytest, pytest-django | 9.2 | 9.2.0, 4.14.0 | **9.1.1**, 4.14.0 | Local drift (BE-CFG-002) |
| Locust | 2.47 | 2.47.0 | **2.46.7** | Local drift (BE-CFG-002) |
| (not documented) | — | not listed | **django-otp 1.7.5** installed | Extra package in the local venv (BE-CFG-002) |
| django-cors-headers | not in the brief | deliberately absent (`requirements.txt:38-39`) | absent | OK (same origin) |
| Server: Gunicorn, Nginx, systemd | yes | — | — | Only on the `setup.sh` path. **Production actually runs on a hosting panel** with the panel's own nginx (`deploy/qa/README.md:178-182`). See BE-SEC-003. |

**Done well:** every package is pinned exactly, one requirements file serves every environment, and no code is vendored.

---

## 2. Settings matrix

All settings are in `grama/settings.py`. "Dev" means `DEBUG` is on or unset. "Prod" means `DEBUG=False`, which also covers the qa server. "Tests" means `pytest` is in `sys.modules` (line 30), on top of Dev or Prod.

| Setting | Dev (old "dev") | Prod (old "prod") | Tests (old "test") | Evidence |
|---|---|---|---|---|
| `DEBUG` | env, **default True** | env `DEBUG=False` | follows env | :27 (BE-SEC-009) |
| `SECRET_KEY` | insecure dev default | required; strength-checked (`django-insecure-`, length < 50, < 5 distinct characters, `change-me`) | as Dev | :37-55 |
| `ALLOWED_HOSTS` | localhost, 127.0.0.1, [::1], 10.0.2.2 | env, default `[]` | as Dev | :41-44 |
| `CSRF_TRUSTED_ORIGINS` | env, `[]` | env, `[]` | — | :57 |
| CORS | not installed | not installed | — | `requirements.txt:38` |
| `DATABASES` | SQLite (WAL, IMMEDIATE, timeout 20), or `DATABASE_URL` if set | `DATABASE_URL` required; MySQL gets utf8mb4 and STRICT_TRANS_TABLES | SQLite **only in the Dev branch** | :64-93 (BE-TEST-001) |
| `CONN_MAX_AGE` | 60 (`DB_CONN_MAX_AGE`) plus `CONN_HEALTH_CHECKS`, URL databases only; SQLite 0 | 60 plus health checks | 0 | :74-75 |
| `CACHES` | default LocMem; `throttle` LocMem | default **LocMem**; `throttle` DatabaseCache `grama_throttle_cache` | as Dev | :153-162 (BE-SEC-015) |
| `MEDIA_ROOT` / `STATIC_ROOT` | `media/`, `staticfiles/` | env, required | `.test-media/` | :110-117 |
| `SERVE_FILES` | default True | default False | as Dev | :113, :117 |
| Secure cookies | env, default False | True | as Dev | :125-126, :138-139 |
| `SECURE_PROXY_SSL_HEADER` | only if `TRUST_PROXY_HTTPS` | X-Forwarded-Proto | — | :127, :140-141 |
| `SECURE_SSL_REDIRECT` | — | env, default False (nginx redirects) | — | :129 |
| HSTS | 0 | 31,536,000 s; subdomains and preload off | — | :130-132, :146 |
| Silenced checks | none | W008, W005, W021 | — | :133 |
| `SECURE_REFERRER_POLICY` | Django default | same-origin | — | :134 |
| `TRUST_X_REAL_IP` | False | True | — | :128, :142 |
| `CITIZEN_INTEGRITY_REQUIRED` | False | True by default, **but False on panel production** (owner decision 2026-11-04, `deploy/qa/BVT-deploy.sh:232`) | — | :135, :143 |
| `API_DOCS_PUBLIC` | True | False | — | :136, :144 |
| Password hashers | Argon2, PBKDF2, PBKDF2-SHA1, Scrypt | same | **MD5** | :171-183 (BE-SEC-010) |
| Email, push, transliteration | SMTP, FCM, Google Input Tools | same | locmem, LocmemPushGateway, off | :171-187 |
| Logging | console only; root `LOG_LEVEL` (INFO); `django.request` and `axes` at WARNING | same | same | :537-551 |
| `REST_FRAMEWORK` renderers | JSON plus BrowsableAPI | JSON only | JSON only | :339-356 |
| `SIMPLE_JWT` | access 15 min, refresh 30 days, rotation and blacklist, `UPDATE_LAST_LOGIN` | same | same | :358-367 |
| `AXES_*` | 5 failures, 15 min, `[username, ip]`, reset on success, custom IP and username callables | same | same | :285-295 |
| `Q_CLUSTER` | ORM broker, 2 workers, timeout 120, retry 180, max_attempts 3, queue_limit 50, bulk 10 | same | `sync=True` | :384-396 |
| Session and CSRF | 30 min sliding (`SESSION_SAVE_EVERY_REQUEST`), HttpOnly, SameSite Lax; CSRF cookie readable by JavaScript | same | same | :302-308 |
| Upload limits | `MAX_IMAGE_UPLOAD_MB` 10, `MAX_VIDEO_UPLOAD_MB` 50, ads 5/60, logos 2, contacts import 2 MiB / 5,000 rows. **`DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE` are not set** (Django defaults). | same | same | :418-490 (BE-CFG-001) |
| Language and time | `te` default, `te`/`en`, Asia/Kolkata, `USE_TZ` | same | same | :315-323 |

**Platform rules (shared by all modes):**
- `ADMIN_POSTS_AUTO_PUBLISH=True`, `REVIEW_FIRST_DECISION_WINS=True`, `REVIEW_ESCALATION_HOURS=24`.
- **`SUPER_ADMIN_CAN_POST=True`** (:406). `.claude/project.md` says False; see BE-DOC-001.
- `MAX_SUPER_ADMINS=5`, `MAX_PHOTOS_PER_POST=5`, `POST_MAX_TARGETS=200`.
- **`PUSH_ALL_POSTS_HIGH_PRIORITY=True`** (:518). `.claude/architecture.md` says only urgent posts go at high priority.
- `CITIZEN_REGISTRATIONS_PER_IP_PER_DAY=0`, which means unlimited (:478). `CITIZEN_MAX_PHOTOS=0`, also unlimited (:477).

**Done well:**
- Production refuses to start with a weak key or with missing DATABASE_URL, MEDIA_ROOT or STATIC_ROOT.
- MySQL is forced to utf8mb4.
- `PUBLIC_BASE_URL`, `FCM_TOPIC_PREFIX` and `ANDROID_CERT_SHA256` are validated at startup (:434-451, :515).
- `check --deploy` in Prod mode reports **0 security warnings** beyond the 3 silenced ones (command run).

---

## 3. Middleware and REST_FRAMEWORK configuration

### INSTALLED_APPS (order, `grama/settings.py:194-228`)

1. Django: `admin`, `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`
2. Third party: `axes`, `rest_framework`, `rest_framework_simplejwt.token_blacklist`, `drf_spectacular`, `django_q`
3. Local: `apps.accounts`, `geography`, `posts`, `feed`, `notifications`, `audit`, `web_public`, `web_admin`, `contacts`, `citizens`, `service_requests`, `ads`, `bills`, `share`

### MIDDLEWARE (order, :230-241)

1. SecurityMiddleware
2. ConditionalGetMiddleware
3. SessionMiddleware
4. LocaleMiddleware
5. CommonMiddleware
6. CsrfViewMiddleware
7. AuthenticationMiddleware
8. MessageMiddleware
9. XFrameOptionsMiddleware
10. AxesMiddleware (last, as django-axes recommends)

There is no GZip middleware; nginx compresses. There is no custom request-logging middleware.

### AUTHENTICATION_BACKENDS (:271-274)

`axes.backends.AxesStandaloneBackend`, then `apps.accounts.backends.OfficialBackend`.

### REST_FRAMEWORK (:330-356)

| Key | Value |
|---|---|
| Authentication | `apps.accounts.authentication.GramaJWTAuthentication`, `GramaSessionAuthentication`. Both check `token_version` and the `must_change_password` gate; the session one also enforces CSRF and needs `access_web_console` (`authentication.py:29-35, 57-62, 75, 109-118`). |
| Permission | `IsAuthenticated` |
| Throttle classes | **None by default.** Only the rate `transliterate: 60/min` is defined. Throttled in code: `LoginThrottle`, 30/hour per IP on the `throttle` cache (`accounts/views_auth.py:58-69`), and `TransliterateThrottle` (`web_admin/api.py:427`). Custom limiters: django-axes; an IP-wide cap of 50 failures per 60 min (`lockout.py:89-104`); the wrong-current-password counter (`credentials.py:168-189`); 5 contact messages per IP per hour (`web_public/api.py:121-131`); citizen posts (5/day) and service requests (10/day) per phone. |
| Pagination | `grama.pagination.PageNumberPagination`, `PAGE_SIZE` 20, `page_size` query parameter, max 100 (`grama/pagination.py:4-9`). Cursor pagination for the feed (`feed/views.py:41`) and the inbox (`notifications/views.py:89`). |
| Exception handler | `grama.exceptions.exception_handler`, which gives the `{"error": {...}}` shape. Django's core `ValidationError` is not mapped (`grama/exceptions.py:85-118`); see BE-COR-001. |
| Renderers | `grama.renderers.JSONRenderer` (IST offsets), plus BrowsableAPI in Dev |
| Parsers | JSON, Form, MultiPart |
| Other | `UNICODE_JSON` True, AutoSchema, `TEST_REQUEST_DEFAULT_FORMAT` json |

### SPECTACULAR_SETTINGS (:369-377)

- `SERVE_PERMISSIONS` is `CanReadApiDocs`. It is open while `API_DOCS_PUBLIC` is on; otherwise only console users (`accounts/permissions.py:72-84`).
- `COMPONENT_SPLIT_REQUEST` is on.

---

## 4. App-by-app inventory

Legend: on_delete values are written C (CASCADE), P (PROTECT) and SN (SET_NULL). "Idx" means Meta.indexes. "UQ" means unique or UniqueConstraint.

### 4.1 accounts (9 migrations; 4,182 source lines)

**Models**

- **`User`** (`models.py:64`)
  - Keys and login: UUID primary key; `username` unique, with regex `^[a-z0-9@._+-]{3,150}$`, lower-cased on save (:85, :157-161).
  - Contact details: `email` nullable; `mobile` unique, nullable, with regex `^[6-9]\d{9}$`.
  - Other fields: `name`, `name_te`; `is_active` (db_index); `must_change_password`, `password_changed_at`, `token_version`; `created_by` FK self SN.
  - Meta: ordering `name`, 10 custom permissions (:140-152).
- **`RoleGroup`** (:173)
  - OneToOne to Group (C, primary key); `key` unique; `level`; `rank` unique; `platform_wide`, `manage_permission`, `requires_setting`, `password_min_length`.
- **`RoleAssignment`** (:237)
  - `user` FK C; `group` FK P; `area_level` and `area_id` (no foreign key); `is_active`; `assigned_by` SN.
  - UQ (user, group, area_level, area_id). Idx (area_level, area_id) and (group, area_level, area_id).
  - `clean()` checks that the area exists (:287-296).

**Serializers:** 27 (`serializers.py`, 461 lines), for example `MeSerializer` :165, `UserCreate` :373 and `UserUpdate` :413.

**Views**
- `views_auth.py`: LoginView :96, RefreshView :110, LogoutView :123, CsrfView :142, SessionLoginView :152, SessionLogoutView :174, PasswordChangeView :185, MeView :223.
- `views_users.py`: UserViewSet :124, RoleAssignmentViewSet :367.

**URLs:** `accounts/urls.py:16-31`, with a SimpleRouter for `users`.

**Permissions:** `HasPerm` :31, `ManagesUsers` :61, `CanReadApiDocs` :72 (`permissions.py`). The logic is in `authz.py` (522 lines): `can` :235, `area_q` :247, `outranks` :383, `can_assign` :403, `can_act_on` :426, `manageable_users` :448.

**Signals**
- The `super_admin_added` signal (`signals.py:25`).
- RoleAssignment post_save and post_delete keep group membership in step (:62, :68).
- Changes to a group's permissions (m2m) are audited (:77).
- Login failures and lockouts are audited (`lockout.py:121, 136`).
- On login, the session stores the token version (`lockout.py:154`).
- A post_migrate sync runs (`apps.py:16`).

**Tasks:** none.

**Admin:** User, RoleAssignment, RoleGroup (read-only) and Group (`admin.py:68-126`).

**Commands:** `create_super_admin`, `seed_sample_data` (297 lines), `load_demo_area`, `sync_role_permissions`. The two seed commands have no production guard.

### 4.2 geography (4 migrations; 2,370 lines)

**Models**
- **`Area`** (abstract, `models.py:50`): `name_en`, `name_te`, `name_key` (db_index), `lgd_code` (unique, nullable), `is_active`. Ordering `name_en`.
- **`State`** (:98).
- **`District`** (:113): `state` FK P. UQ (state, name_key).
- **`Mandal`** (:131): `district` FK P. UQ (district, name_key).
- **`Village`** (:159): `mandal` FK P; latitude and longitude Decimal(9,6) with validators; `pin_code`; `census_name`. Idx (latitude, longitude). UQ (mandal, name_key).

**Serializers:** `serializers.py` (public) and `serializers_admin.py` (16 classes).

**Views**
- `views.py`: public lists, `NearestVillageView` :130 (bounding box on the lat/lng index, `nearest.py:63-81`).
- `views_admin.py`: `PublicReadAdminWrite` (:78-98) leaves GET public and requires `geography.manage_master_data` (PLATFORM) for writes.

**Services:** `create_area` :88, `update_area` :112, `import_villages` :144. Each is atomic, audited, and maps `IntegrityError` to 409. `importer.py` (519 lines) writes `State` directly (:484-492, documented).

**Admin:** all four models, with `list_select_related`.

**Commands:** `import_geography`, `geo_duplicates`, `geo_coordinates`, `geo_clean_names`, `geo_area_usage`.

**Signals and tasks:** none.

### 4.3 posts (9 migrations; 2,789 lines)

**Models**
- **`Category`** (:24): `key` unique.
- **`Post`** (:69)
  - Relations: UUID primary key; `author` FK→User P (nullable); `citizen` FK→CitizenDevice P (nullable); `category` FK P; `decided_by` SN.
  - Status and dates: `status` (default DRAFT); `published_at` (db_index); `share_code` unique (nullable).
  - Areas: `target_level` and `target_area_id` (no foreign key).
  - Idx: (status, target_level, target_area_id, published_at), (status, submitted_at), (author, status), (status, event_start), (status, is_urgent, published_at).
  - Check constraints: event end after start; author or citizen (:120-131).
  - 12 permissions.
- **`PostMedia`** (:194): post C; image, medium, video and thumbnail files.
- **`ReviewAction`** (:229): post C; actor SN. Idx (post, created_at).
- **`PostTarget`** (:258): post C. UQ (post, area_level, area_id). Idx (area_level, area_id, post).
- **`PostTranslation`** (:283): UQ (post, language).
- **`SettingOverride`** (:306): `key` unique, JSON value.

**Serializers:** 22 (`serializers.py`, 363 lines). `PostSerializer` (:165-270) is the largest, with 13 method fields.

**Views:** `views.py` (477 lines). `PostViewSet` :172-353 has 15 actions. Also `ReviewQueueView` :359, `StatsView` :396, `AdminSettingsView` :445, `AdminCategoryViewSet` :98, `CategoryListView` :74 and `AppConfigView` :83.

**Services:** `services.py` (765 lines, the largest file) holds the state machine. Every move is a conditional UPDATE inside `transaction.atomic`, followed by `_log` (a ReviewAction plus an audit entry) and a signal sent on commit (:280-281, :340-765). **Grep found no post status writes outside `services.py`.**

**Signals:** `post_published`, `post_submitted`, `post_decided`, `post_escalated` (`signals.py:16-19`). The receiver `translate_on_publish` is in `receivers.py:14`.

**Tasks:** `translate_post` :18, `_retry_later` (one-off Schedules) :49, `run_escalation` :79, `ensure_escalation_schedule` :84.

**Admin:** none.

**Commands:** `ensure_schedules`, `translate_published_posts`, `backfill_medium_images`.

### 4.4 feed (0 migrations; 406 lines)

- No models.
- `FeedCursorPagination` (`views.py:41`). Media counts and URLs are filled in by subqueries (:49-70). The village filter is an EXISTS on the PostTarget index.
- Views: FeedView :123, FeedPostDetailView :155, FeedPostsByIdView :165 (at most 50 ids), FeedShareView :198, UpcomingView :213, UrgentView :227.
- Cache-Control is `public, max-age=60`.

### 4.5 notifications (12 migrations; 1,254 lines)

**Models**
- **`Device`** (:16): `fcm_token` unique; `user` SN; `village_id` integer. Idx (user) and (village_id).
- **`Notification`** (:53): user C; post SN; service_request SN. Idx (user, created_at) and (user, read_at).
- **`NotificationTemplate`** (:96): `key` unique.

**Views:** DeviceRegisterView :35, DeviceDetailView :57, NotificationListView :94 (cursor), read :115, read-all :127, template list :142 and detail :149.

**Receivers:** five, each wrapped in `_safely` (`receivers.py:18-64`).

**Tasks:** `send_push_to_topic` :31, `send_push_to_topics` :44, `send_push_to_citizen` :49, `send_push_to_user` :69, queued with `async_task(group="push")`.

**Admin:** Device, Notification and NotificationTemplate.

### 4.6 audit (2 migrations; 352 lines)

- **`AuditLog`** (`models.py:39`): UUID; `created_at`, `action` and `type` each with db_index; actor SN. Idx (type, created_at), (actor, created_at), (area_level, area_id), (target_type, target_id). `save()` and `delete()` refuse changes to existing rows (:75-81).
- **Writing:** the only writer is `services.record` (:14). It does no masking (BE-SEC-001).
- **Views:** AuditListView :110 and AuditExportView :158 (capped at 50,000 rows, formula-safe CSV).
- **Admin:** read-only.

### 4.7 web_public (3 migrations; 390 lines)

- **`ContactMessage`** (:6): `mobile`, `ip_address`, `created_at` (db_index), `handled_by` SN. Idx (ip_address, created_at).
- **Views** (`api.py`): SiteConfigView :96, ContactMessageView :109 (honeypot plus the per-IP limit), ShowcaseView :152, ContactMessageListView :220, ContactMessageDetailView :246.
- **Website files:** `views.py:6`.

### 4.8 web_admin (1 migration; 613 lines)

- **`ConsoleAccess`** (unmanaged): holds the console, dashboard and reports permissions.
- **Views** (`api.py`, 452 lines): DashboardView :115, ReportView :323, ReportExportView :377, TransliterateView :435 (with `HasAnyRole` :416 and a throttle).
- **Transliteration:** `transliteration.py`; the cache key is a hash and the text is never logged.

### 4.9 contacts (3 migrations; 1,362 lines)

**Models**
- **`ContactType`** (:37): `key` unique; soft delete; `deleted_by` SN.
- **`Contact`** (:72): type FK P; `phone`; `area_level` and `area_id`; created, updated and removed by SN. Idx (is_active, area_level, area_id), (type, is_active), (phone). **No UQ on type, phone and area.** `__str__` includes the full phone number (:114).

**Services:** `services.py` (547 lines) is the only writer. It masks phones in the audit log and uses `select_for_update` together with conditional guards.

**Views:** `views_public.py` (CitizenContactsView :44, ContactTypeListView :66) and `views_admin.py` (AdminContactViewSet :61, ContactImportView :141, AdminContactTypeViewSet :171).

**Commands:** `import_contacts`, `seed_helplines`.

### 4.10 citizens (2 migrations; 922 lines)

- **`CitizenDevice`** (`models.py:16`): UUID; `token_hash` unique (SHA-256); `registered_ip_hash` and `android_id_hash` (HMAC with SECRET_KEY, db_index); `nonce` unique; `fcm_token` in plain text; block fields; `blocked_by` SN. Idx (registered_ip_hash, created_at).
- **Views:** ChallengeView :40, RegisterView :46, MeView :60, PostsView :87. They have no authenticator and use `AllowAny`; `X-Citizen-Token` is checked in `services.device_from_request` (:105-113).
- **Services:** `services.py` (370 lines).

### 4.11 service_requests (6 migrations; 1,020 lines)

**Models**
- **`Service`** (:38): `key` unique; logo file.
- **`ServiceRequest`** (:98): service FK P (to_field key); `village` P; `device` P; `assigned_to` SN; holds name, phone, email and address. Idx (village, status), (assigned_to, status), (device, created_at).
- **`ServiceRequestEvent`** (:138): request C.

**Views:** PublicServicesView :54, CitizenServiceRequestsView :64, ServiceRequestListView :95, ServiceRequestDetailView :143, AdminServicesView :198, AdminServiceDetailView :215, AdminServiceOrderView :231.

**Services:** the only writer; notifications are sent on commit.

### 4.12 ads (2 migrations; 501 lines)

- **`Ad`** (:24): image and video; `link` (http/https only); `area_level` and `area_id`; dates; `created_by` SN. Idx (is_active, area_level, area_id). Check constraint: end date on or after start date.
- **Views:** PublicAdsView :41 and AdminAdViewSet :56.
- **Services:** the only writer; images are re-encoded as WebP and videos go through `posts.media.prepare_video`.

### 4.13 bills (3 migrations; 333 lines)

- **`Bill`** (:20): `key` unique; logo.
- **Views:** PublicBillsView :28, AdminBillsView :52, AdminBillDetailView :69, AdminBillOrderView :85.

### 4.14 share (0 migrations; 268 lines)

- No models.
- **Views:** `post_page` :97 (strict CSP, regex-checked code), `app_page` :138, `assetlinks` :160.
- **Preview picture:** `og.py` makes the 1200×630 picture the first time a post is shared.
- **Command:** `write_assetlinks`.
- **Templates:** `|safe` and `mark_safe` are not used.

---

## 5. Endpoint inventory and schema differences

**Legend**
- **Auth:** **J/S** means JWT or session (the defaults). **none** means `authentication_classes=[]`. **opt** means the defaults run but `AllowAny` admits anyone. **CT** means `X-Citizen-Token`, checked in code.
- **Roles:** **SA** is Super Admin; **Adm** are District, Mandal and Village Admins; **All5** is SA, Adm and Panchayat members.
- **Throttle:** "—" means no DRF throttle. On the `setup.sh` path, nginx adds `gc_api` (20 r/s) to every `/api/` call and `gc_login` (10 r/min) to the login routes (`deploy/templates/nginx-locations.conf:25-87`). The panel production nginx is not in the repo.
- **Used by** follows `.claude/api.md`. "n/l" means that table does not list the endpoint.

All paths are under `/api/v1/` unless they start with `/`.

| Method | Path | View (file:line) | Auth | Permission classes | Throttle | Pagination | Roles allowed | Used by |
|---|---|---|---|---|---|---|---|---|
| GET | health/ | grama/views.py:22 | none | AllowAny | — | — | anyone | ops (n/l) |
| POST | auth/login/ | accounts/views_auth.py:96 | none | AllowAny | LoginThrottle 30/h/IP; axes; IP cap | — | any active assignment | mobile |
| POST | auth/refresh/ | views_auth.py:110 | none | AllowAny | **—** | — | refresh holder | mobile |
| POST | auth/logout/ | views_auth.py:123 | J/S | IsAuthenticated | — | — | any | mobile |
| GET | auth/csrf/ | views_auth.py:142 | none | AllowAny | — | — | anyone | console |
| POST | auth/session/login/ | views_auth.py:152 | none (CSRF checked by hand) | AllowAny | LoginThrottle; axes | — | `access_web_console` (SA) | console |
| POST | auth/session/logout/ | views_auth.py:174 | none (CSRF checked by hand) | AllowAny | — | — | anyone | console |
| POST | auth/password/change/ | views_auth.py:185 | J/S | IsAuthenticated | wrong-password counter | — | any | both |
| GET | me/ | views_auth.py:223 | J/S | IsAuthenticated | — | — | any | both |
| PATCH | me/ | views_auth.py:223 | J/S | IsAuthenticated | — | — | any | both |
| GET | users/ | views_users.py:229 | J/S | IsAuthenticated + ManagesUsers | — | PageNumber | SA, Adm (`manage_*`) | console, mobile |
| POST | users/ | views_users.py:248 | J/S | + `can_assign`; own password for SA peers | — | — | SA, Adm | console, mobile |
| GET | users/{uuid}/ | views_users.py:238 | J/S | ManagesUsers; queryset `manageable_users` | — | — | SA, Adm | console, mobile |
| PATCH | users/{uuid}/ | views_users.py:288 | J/S | same | — | — | SA, Adm | console, mobile |
| POST | users/{id}/block/ | views_users.py:300 | J/S | + `can_act_on(block_user)` | — | — | SA, Adm | console, mobile |
| POST | users/{id}/unblock/ | views_users.py:318 | J/S | same | — | — | SA, Adm | console, mobile |
| POST | users/{id}/reset-password/ | views_users.py:335 | J/S | + `reset_password` | — | — | SA, Adm | console, mobile |
| POST | users/{id}/unlock/ | views_users.py:354 | J/S | + `unlock_user` | — | — | SA, Adm | console, mobile |
| POST | users/{uuid}/role-assignments/ | views_users.py:385 | J/S | ManagesUsers + `can_assign` | — | — | SA, Adm | console, mobile |
| PATCH | users/{uuid}/role-assignments/{uuid}/ | views_users.py:412 | J/S | + `reassign_user` | — | — | SA, Adm | console, mobile |
| DELETE | users/{uuid}/role-assignments/{uuid}/ | views_users.py:452 | J/S | ManagesUsers (target manageable) | — | — | SA, Adm | console, mobile |
| GET | geo/states/ | geography/views.py:66 | none | AllowAny | — | none | anyone | mobile |
| GET | geo/districts/ | geography/views_admin.py:129 | none (GET) | AllowAny | — | none | anyone | mobile, console |
| POST | geo/districts/ | views_admin.py:129 | J/S | HasPerm `geography.manage_master_data` (PLATFORM) | — | — | SA | console |
| PATCH | geo/districts/{id}/ | views_admin.py:168 | J/S | same | — | — | SA | console |
| GET | geo/mandals/ | views_admin.py:138 | none (GET) | AllowAny | — | none | anyone | mobile, console |
| POST | geo/mandals/ | views_admin.py:138 | J/S | manage_master_data | — | — | SA | console |
| PATCH | geo/mandals/{id}/ | views_admin.py:176 | J/S | manage_master_data | — | — | SA | console |
| GET | geo/villages/ | views_admin.py:147 | none (GET) | AllowAny | — | none (search capped at 50) | anyone | mobile, console |
| POST | geo/villages/ | views_admin.py:147 | J/S | manage_master_data | — | — | SA | console |
| GET | geo/villages/{id}/ | views_admin.py:156 | none (GET) | AllowAny | — | — | anyone | mobile |
| PATCH | geo/villages/{id}/ | views_admin.py:156 | J/S | manage_master_data | — | — | SA | console |
| POST | geo/villages/import/ | views_admin.py:246 | J/S | manage_master_data | — | — | SA | console |
| GET | geo/nearest/ | geography/views.py:130 | none | AllowAny | — | — | anyone | mobile |
| GET | geo/tree/ | views_admin.py:183 | J/S | manage_master_data | — | none | SA | console |
| GET | geo/admin/villages/ | views_admin.py:230 | J/S | manage_master_data | — | none | SA | n/l |
| GET | categories/ | posts/views.py:74 | none | AllowAny | — | none | anyone | console, mobile |
| GET | app-config/ | posts/views.py:83 | none | AllowAny | — | — | anyone | mobile |
| GET | admin/categories/ | posts/views.py:98 | J/S | HasPerm `posts.manage_categories` (PLATFORM) | — | none | SA | console |
| POST | admin/categories/ | posts/views.py:98 | J/S | same | — | — | SA | console |
| PATCH | admin/categories/{id}/ | posts/views.py:98 | J/S | same | — | — | SA | console |
| GET | posts/ | posts/views.py:206 | J/S | IsAuthenticated; SQL scope `visible_posts` | — | PageNumber + status_counts | own posts; reviewers in area | console, mobile |
| POST | posts/ | posts/views.py:228 | J/S | IsAuthenticated; service checks `create_post`/`submit_post` | — | — | All5 (SA while the setting is on) | console, mobile |
| GET | posts/targets/ | posts/views.py:344 | J/S | IsAuthenticated | — | — | All5 | mobile |
| GET | posts/{id}/ | posts/views.py:244 | J/S | 404 outside scope | — | — | as list | both |
| PATCH | posts/{id}/ | posts/views.py:248 | J/S | author (service) | — | — | author | both |
| DELETE | posts/{id}/ | posts/views.py:255 | J/S | author, DRAFT only | — | — | author | both |
| POST | posts/{id}/submit/ | posts/views.py:261 | J/S | `submit_post` | — | — | All5 | both |
| GET | posts/{id}/history/ | posts/views.py:266 | J/S | visible scope | — | none | as list | both |
| POST | posts/{id}/review/ | posts/views.py:273 | J/S | per-action permission over the post's areas | — | — | SA, Adm | both |
| POST | posts/{id}/media/ | posts/views.py:289 | J/S | author, editable | — | — | author | mobile |
| DELETE | posts/{id}/media/{media_id}/ | posts/views.py:349 | J/S | author, editable | — | — | author | mobile |
| POST | posts/{id}/urgent/ | posts/views.py:304 | J/S | `mark_urgent` over the post | — | — | SA, Adm | both |
| POST | posts/{id}/block-citizen/ | posts/views.py:316 | J/S | `posts.block_citizen` (citizens/services.py:289-297) | — | — | SA | both |
| POST | posts/{id}/unblock-citizen/ | posts/views.py:325 | J/S | same | — | — | SA | both |
| POST | posts/{id}/discard-citizen-posts/ | posts/views.py:336 | J/S | same | — | — | SA | both |
| GET | review/queue/ | posts/views.py:359 | J/S | HasPerm `view_review_queue` + SQL scope | — | PageNumber + source_counts | SA, Adm | mobile |
| GET | stats/ | posts/views.py:396 | J/S | HasPerm `view_stats` + `can` on the area | — | — | All5 | mobile |
| GET | admin/settings/ | posts/views.py:445 | J/S | HasPerm `manage_settings` (PLATFORM) | — | — | SA | console |
| PATCH | admin/settings/ | posts/views.py:445 | J/S | same | — | — | SA | console |
| GET | feed/ | feed/views.py:123 | none | AllowAny | — | Cursor (20) | anyone | mobile |
| GET | feed/posts/ | feed/views.py:165 | none | AllowAny | — | none (≤ 50 ids) | anyone | mobile |
| GET | feed/posts/{uuid}/ | feed/views.py:155 | none | AllowAny | — | — | anyone | mobile |
| GET | feed/share/{code}/ | feed/views.py:198 | none | AllowAny | — | — | anyone | mobile |
| GET | feed/upcoming/ | feed/views.py:213 | none | AllowAny | — | limit 10 | anyone | mobile |
| GET | feed/urgent/ | feed/views.py:227 | none | AllowAny | — | limit 5 | anyone | mobile |
| POST | devices/ | notifications/views.py:35 | opt | AllowAny | **—** | — | anyone | mobile |
| PATCH | devices/{uuid}/ | notifications/views.py:70 | opt | AllowAny; owner check if linked | — | — | id holder | mobile |
| DELETE | devices/{uuid}/ | notifications/views.py:81 | opt | same | — | — | id holder | mobile |
| GET | notifications/ | notifications/views.py:94 | J/S | IsAuthenticated, own rows | — | Cursor (20) | any | mobile |
| POST | notifications/read-all/ | notifications/views.py:127 | J/S | IsAuthenticated | — | — | any | mobile |
| POST | notifications/{uuid}/read/ | notifications/views.py:115 | J/S | own row | — | — | any | mobile |
| GET | admin/notification-templates/ | notifications/views.py:142 | J/S | HasPerm `manage_templates` (PLATFORM) | — | none | SA | console |
| PATCH | admin/notification-templates/{key}/ | notifications/views.py:149 | J/S | same | — | — | SA | console |
| GET | audit/ | audit/views.py:110 | J/S | HasPerm `audit.view_audit_log` + `in_reach` filter | — | PageNumber | SA | console, mobile |
| GET | audit/export/ | audit/views.py:158 | J/S | HasPerm `audit.export_audit_log` | — | none (≤ 50,000 rows) | SA | console |
| GET | public/site-config/ | web_public/api.py:96 | none | AllowAny | — | — | anyone | website, mobile |
| POST | public/contact-messages/ | web_public/api.py:109 | none | AllowAny | 5/IP/h (custom) + honeypot | — | anyone | website |
| GET | public/showcase/ | web_public/api.py:152 | none | AllowAny | — | ≤ 10 | anyone | website |
| GET | admin/contact-messages/ | web_public/api.py:220 | J/S | HasPerm `manage_contact_messages` (PLATFORM) | — | PageNumber | SA | console |
| PATCH | admin/contact-messages/{id}/ | web_public/api.py:246 | J/S | same | — | — | SA | console |
| GET | admin/dashboard/ | web_admin/api.py:115 | J/S | HasPerm `view_dashboard` (PLATFORM) | — | — | SA | console, mobile |
| GET | admin/reports/summary/ | web_admin/api.py:323 | J/S | HasPerm `view_reports` (PLATFORM) | — | none | SA | console |
| GET | admin/reports/posts-by-area/ | web_admin/api.py:323 | J/S | same | — | none | SA | console |
| GET | admin/reports/review-time/ | web_admin/api.py:323 | J/S | same | — | none | SA | console |
| GET | admin/reports/admin-activity/ | web_admin/api.py:323 | J/S | same | — | paged in Python | SA | console |
| GET | admin/reports/{report}/export/ | web_admin/api.py:377 | J/S | same | — | none | SA | console |
| GET | admin/transliterate/ | web_admin/api.py:435 | J/S | HasAnyRole | `transliterate` 60/min per user | — | All5 | console, mobile |
| GET | contacts/ | contacts/views_public.py:44 | none | AllowAny | — | none (bounded by capacity) | anyone | mobile |
| GET | contact-types/ | contacts/views_public.py:66 | none | AllowAny | — | none | anyone | mobile |
| GET | admin/contacts/ | contacts/views_admin.py:99 | J/S | HasPerm `contacts.manage_contact` + `area_q` | — | PageNumber | All5 | console, mobile |
| POST | admin/contacts/ | contacts/views_admin.py:104 | J/S | same + area check | — | — | All5 | console, mobile |
| GET | admin/contacts/targets/ | contacts/views_admin.py:136 | J/S | same | — | — | All5 | mobile |
| GET | admin/contacts/{uuid}/ | contacts/views_admin.py:111 | J/S | same; 404 outside the area | — | — | All5 | console, mobile |
| PATCH | admin/contacts/{uuid}/ | contacts/views_admin.py:121 | J/S | same | — | — | All5 | console, mobile |
| DELETE | admin/contacts/{uuid}/ | contacts/views_admin.py:128 | J/S | same | — | — | All5 | console, mobile |
| POST | admin/contacts/import/ | contacts/views_admin.py:153 | J/S | HasPerm `manage_contact` (PLATFORM) | — | — | SA | console |
| GET | admin/contact-types/ | contacts/views_admin.py:171 | J/S | HasPerm `manage_contact_types` (PLATFORM) | — | none | SA | console |
| POST | admin/contact-types/ | contacts/views_admin.py:171 | J/S | same | — | — | SA | console |
| PATCH | admin/contact-types/{id}/ | contacts/views_admin.py:193 | J/S | same | — | — | SA | console |
| DELETE | admin/contact-types/{id}/ | contacts/views_admin.py:203 | J/S | same | — | — | SA | console |
| POST | citizen/challenge/ | citizens/views.py:40 | none | AllowAny | **—** | — | anyone | mobile |
| POST | citizen/register/ | citizens/views.py:46 | none | AllowAny | per-IP only if the setting is > 0 (default 0, **off**) | — | nonce holder | mobile |
| GET | citizen/me/ | citizens/views.py:60 | CT | AllowAny | — | — | token holder | mobile |
| PATCH | citizen/me/ | citizens/views.py:60 | CT | AllowAny | — | — | token holder | mobile |
| GET | citizen/posts/ | citizens/views.py:90 | CT | AllowAny | — | slice of 50 | token holder | mobile |
| POST | citizen/posts/ | citizens/views.py:101 | CT | AllowAny | 5/day per phone (runtime setting) | — | token holder | mobile |
| GET | services/ | service_requests/views.py:54 | none | AllowAny | — | none | anyone | mobile |
| GET | citizen/service-requests/ | service_requests/views.py:70 | CT | AllowAny | — | slice of 50 | token holder (`same_phone`) | mobile |
| POST | citizen/service-requests/ | service_requests/views.py:81 | CT | AllowAny | 10/day per phone | — | token holder | mobile |
| GET | service-requests/ | service_requests/views.py:95 | J/S | HasPerm `view_service_requests` + `visible_requests` | — | PageNumber + status_counts | All5 | console, mobile |
| GET | service-requests/{id}/ | service_requests/views.py:143 | J/S | same; 404 outside the area | — | — | All5 | console, mobile |
| PATCH | service-requests/{id}/ | service_requests/views.py:143 | J/S | + assignee via `allowed_actions` | — | — | assignee | console, mobile |
| GET | admin/services/ | service_requests/views.py:198 | J/S | HasPerm `manage_services` (PLATFORM) | — | none | SA | console, mobile |
| POST | admin/services/ | service_requests/views.py:198 | J/S | same | — | — | SA | console, mobile |
| POST | admin/services/order/ | service_requests/views.py:231 | J/S | same | — | — | SA | console, mobile |
| PATCH | admin/services/{key}/ | service_requests/views.py:215 | J/S | same | — | — | SA | console, mobile |
| GET | ads/ | ads/views.py:41 | none | AllowAny | — | limit 10 | anyone | mobile |
| GET | admin/ads/ | ads/views.py:56 | J/S | HasPerm `ads.manage_ad` (PLATFORM) | — | PageNumber | SA | mobile |
| POST | admin/ads/ | ads/views.py:56 | J/S | same | — | — | SA | mobile |
| GET | admin/ads/{id}/ | ads/views.py:56 | J/S | same | — | — | SA | mobile |
| PATCH | admin/ads/{id}/ | ads/views.py:56 | J/S | same | — | — | SA | mobile |
| DELETE | admin/ads/{id}/ | ads/views.py:56 | J/S | same | — | — | SA | mobile |
| GET | bills/ | bills/views.py:28 | none | AllowAny | — | none | anyone | mobile |
| GET | admin/bills/ | bills/views.py:52 | J/S | HasPerm `bills.manage_bills` (PLATFORM) | — | none | SA | console, mobile |
| POST | admin/bills/ | bills/views.py:52 | J/S | same | — | — | SA | console, mobile |
| POST | admin/bills/order/ | bills/views.py:85 | J/S | same | — | — | SA | console, mobile |
| PATCH | admin/bills/{key}/ | bills/views.py:69 | J/S | same | — | — | SA | console, mobile |
| GET | /api/schema/ | grama/urls.py:45 | J/S | CanReadApiDocs | — | — | anyone (dev, qa); console users (prod) | developers |
| GET | /api/docs/ | grama/urls.py:46 | J/S | CanReadApiDocs | — | — | same | developers |
| GET | /p/{code}/ | share/views.py:97 | Django view, none | — | — | — | anyone | sharing |
| GET | /app/ | share/views.py:138 | none | — | — | — | anyone | invite link |
| GET | /.well-known/assetlinks.json | share/views.py:160 | none | — | — | — | anyone | Android |
| GET | /django-admin/… | Django admin (grama/urls.py:47) | Django session | staff | axes | — | developers | nginx `OFFICE_IPS` only (setup.sh path) |
| GET | /console/…, /media/…, /static/…, / | web_admin/views.py:6, grama/sitefiles.py:54-74, web_public/views.py:6 | none | SERVE_FILES only | — | — | — | — |

**Counts.**
- The URL resolver lists 296 patterns: 102 DRF routes under `api/v1/`, 2 schema routes, Django admin, the share routes and file serving.
- drf-spectacular lists **128 operations on 100 paths**, measured with `manage.py spectacular --format openapi-json` and a JSON walk.
- Every DRF route the resolver lists appears in the schema. The non-DRF routes are not in it, as expected: `/p/`, `/app/`, assetlinks, console, media, static and `/django-admin/`.

### Schema differences and quality (BE-API-001, BE-API-002)

**Errors** (graceful fallback, no response schema): `DashboardView` (`web_admin/api.py:115`), `ReportView` (:323), `AppConfigView` (`posts/views.py:82`) and `StatsView` (`posts/views.py:396`).

**Warnings**
- **Component name collision:** `PatchedDeviceUpdateRequest` comes from both `apps.notifications.serializers.DeviceUpdateSerializer` and `apps.citizens.serializers.DeviceUpdateSerializer`. Spectacular says this "will very likely result in an incorrect schema".
- The `PostViewSet` path parameter `id` is untyped (it defaults to string).
- `AuditEntrySerializer.get_area` has no type hint.
- 13 enum naming collisions (`level`, `icon`, `language`, `status`, `kind`, `to_status`).

**Contract vs schema** (regex diff of "METHOD path" in `docs/api-contract.md`):
- `GET health/` is not in the contract.
- The contract names two endpoints that do not exist: `GET /geo/villages/nearest` (`api-contract.md:811`; the real path is `geo/nearest/`) and `GET /feed/summary` (:813).

**`.claude/api.md` "who calls what":**
- `geo/admin/villages/`, `health/` and the console's use of `review/queue/` are not listed.
- `admin/ads/*` is listed for mobile only.

**Tooling:** on Windows, `manage.py spectacular` crashes with `UnicodeEncodeError` (cp1252 console vs Telugu text) unless `PYTHONIOENCODING=utf-8` is set (BE-API-003).

---

## 6. Roles and permission model

| Role key | Level | Rank | Platform-wide | Manage permission | Shortest password | Defined |
|---|---|---|---|---|---|---|
| SUPER_ADMIN | STATE | 0 | yes | `accounts.manage_super_admin` | 12 | `apps/accounts/role_matrix.py:85-114` |
| DISTRICT_ADMIN | DISTRICT | 1 | no | `manage_district_admin` | 12 | :124-134 |
| MANDAL_ADMIN | MANDAL | 2 | no | `manage_mandal_admin` | 12 | :144-153 |
| VILLAGE_ADMIN | VILLAGE | 3 | no | `manage_village_admin` | 12 | :163-171 |
| PANCHAYAT_MEMBER | VILLAGE | 4 | no | `manage_panchayat_member` | 8 | :182 |
| Citizen | the phone's village | — | — | — | — | Not a role: an install token (`citizens.CitizenDevice`) |

### Permission bundles (`role_matrix.py:38-80`)

- **POSTING** goes to all five roles. For SA it depends on `SUPER_ADMIN_CAN_POST` (`requires_setting`, :114).
- **REVIEWING** (review, publish, recheck, discard, takedown, reverse, review queue, mark urgent) goes to SA and Adm.
- **USER_ACTIONS** (block, reset password, reassign, unlock) go to SA and Adm.
- **PLATFORM_ADMIN** goes to SA only. It covers master data, audit view and export, console, dashboard, reports, templates, contact messages, contact types, categories, settings, ads, services, bills and groups.
- `contacts.manage_contact` and `service_requests.view_service_requests` go to all five roles. `manage_service_requests` goes to SA and Adm. `posts.block_citizen` goes to SA only.

### How roles are stored and kept in step

A role is a Django `Group` plus a `RoleGroup` row holding its level and rank. A grant is a `RoleAssignment` (user, group, area).

`sync.py:78-145` applies the matrix:
- It is idempotent and parks ranks while it moves them.
- It raises an error for a permission no model declares.
- It runs after every `migrate` and through `sync_role_permissions`.

### How roles are given

- `POST users/` creates the user and the assignment in one transaction (`views_users.py:271-284`).
- `role-assignments/` POST, PATCH and DELETE (`views_users.py:385-465`).
- The `create_super_admin` command.
- Django admin (inline and model admin; `manage_groups` only, `admin.py:25-45`).
- Group membership follows the active assignments (`signals.py:50-74`).

### How access is checked

- `HasPerm(codename, scope)` on views (`permissions.py:31-58`). It asks `authz.can`, which needs an active user with no pending password change and a grant whose area covers the target (`authz.py:205-214, 235`). Grants are cached on the user object.
- Lists are filtered in SQL: `authz.area_q` (:247), `manageable_users` (:448), `posts.services.visible_posts`, `service_requests.selectors.visible_requests`.
- Actions on other users go through `can_act_on` (:426) and `can_assign` (:403), and need a strictly higher rank. Super Admins are peers of each other (`outranks` :383-390), with the own-password check, "never the last one" and `MAX_SUPER_ADMINS` (`peers.py:52-81`).
- Session and token revocation: `token_version` in the JWT "tv" claim and in the session (`tokens.py:30-82`, `authentication.py:109-111`). Blocking, a reset or a password change bumps it and blacklists every refresh token.
- **Grep found no group-name or group-key checks outside `role_matrix.py`, data migrations and the sync.**

**Gap:** `HasPerm.has_object_permission` answers 403 `out_of_area` (`permissions.py:54-57`), while the convention says 404 for objects outside reach. No view in the reviewed scope relies on it (BE-SEC-016).

**Done well:**
- Permission and area are two separate checks.
- Matrix-style tests work out the expected answers on their own (`apps/accounts/tests/test_matrix.py`; 1,651 collected tests in accounts).

---

## 7. External integrations

| Integration | Called from | Timeout | Retries | Credentials | Logging |
|---|---|---|---|---|---|
| FCM HTTP v1 | `notifications/push.py:149-164` (FcmGateway), from the tasks in `notifications/tasks.py` | 10 s (:155-156) | `grama/retry.py:29-49`: 3 attempts, backoff that **sleeps in the worker** (1 s, then 4 s). A final failure is audited as `push.failed`. | `FCM_CREDENTIALS_FILE` (service-account JSON path), `FCM_PROJECT_ID`. Disabled with a warning if missing (:131-135). | Target and exception type only (`tasks.py:23`). A new AuthorizedSession on every task (`get_push_gateway`, :167). |
| Google Translate v2 / Bhashini | `posts/translation.py:78-136`, from `tasks.translate_post` on `post_published` | `TRANSLATION_TIMEOUT_S`=20 | One-off Schedules after 1, 5 and 15 min (`tasks.py:49-66`) | `GOOGLE_TRANSLATE_KEY` (sent as the `X-goog-api-key` header, not in the URL), `BHASHINI_*` env. The Bhashini endpoint and key are cached per process (:94, :117). | Post id and exception type. **Off by default** (`TRANSLATION_PROVIDER=off`). |
| Google Input Tools (Telugu suggestions) | `web_admin/transliteration.py:34-38`, from `admin/transliterate/` | `TRANSLITERATION_TIMEOUT_S`=4 | none | no key (public endpoint) | Text never logged; the cache key is a hash. Officials' names are sent to Google (documented). |
| Play Integrity | `citizens/integrity.py:70-118`, synchronously in `citizen/register/` | 10 s (:87-90) | none | `PLAY_INTEGRITY_CREDENTIALS_FILE`, else `FCM_CREDENTIALS_FILE` (:75), loaded on every call | On refusal: a warning including `response.text[:200]`. Checks package, nonce, timestamp, `PLAY_RECOGNIZED` and `MEETS_DEVICE_INTEGRITY`; **not** the licensing verdict or the certificate digest. **Off on panel production** (owner decision). |
| ffprobe / ffmpeg | `posts/media.py:182-230` (posts, citizen media, ad videos) | 30 s, 60 s, 120 s | none; `TimeoutExpired` is not caught (likely a 500, not verified) | — | Argument lists, no shell |

Pillow uploads are guarded: decompression-bomb warnings become errors, there is a 40 MP header check, an allow-list of formats, EXIF transpose, and re-encoding to WebP (`posts/media.py:39-97`, `grama/logos.py:32-58`).

---

## 8. Background jobs

**Broker.** django-q2 uses the Django ORM as its broker (`Q_CLUSTER`, `settings.py:384-396`):
- 2 workers (`Q_WORKERS`), timeout 120 s, retry 180 s, `max_attempts` 3, `queue_limit` 50, `bulk` 10, `catch_up` False.
- Synchronous under tests.
- No Redis.

**Workers.**
- On the `setup.sh` path: `gramaconnect-qcluster.service`, with `Restart=always`, sandboxed like Gunicorn.
- On the panel: started by `BVT-start.sh`, with a keep-alive cron job every minute.

| Job | Function | Trigger |
|---|---|---|
| Escalation of overdue pending posts | `apps.posts.tasks.run_escalation` (`tasks.py:79`), which runs `services.escalate_overdue` (:721-765) | Schedule "escalate-overdue-posts", every `ESCALATION_CHECK_MINUTES` (15), created by `manage.py ensure_schedules` |
| Daily housekeeping | `grama.housekeeping.run_housekeeping` (:27-49). Deletes failed tasks older than 30 days, expired outstanding JWTs, expired sessions and the axes failure log. | Schedule "daily-housekeeping", DAILY, same command |
| Translation | `apps.posts.tasks.translate_post` | `async_task` on `post_published`; retries as `Schedule.ONCE` jobs |
| Push | `send_push_to_topic`, `send_push_to_topics`, `send_push_to_citizen`, `send_push_to_user` | `async_task(group="push")` from `notifications/services.py:135-324` |

**Observations:**
- `notify_users` renders, creates a Notification and enqueues **for each recipient, inside the request** after commit (`notifications/services.py:118-143`); see BE-PERF-001.
- Posts that have already escalated to STATE are picked up again on every run and skipped (`services.py:739-740`).
- The schedules only exist if `ensure_schedules` has run. That is a deploy step; whether it has run on the servers is not verified.

---

## 9. Deployment and server configuration

There are **two deployment paths** in `deploy/`.

### A. Root-server path

Files: `setup.sh`, `deploy.sh`, `backup.sh`, `restore.sh`, `templates/`.

**Gunicorn** (`templates/gramaconnect-gunicorn.service`)
- Unix socket; workers 2×CPU+1, capped at 9 (`setup.sh:324-326`).
- `--timeout 300` for every request (:21); `max-requests` 2000 with jitter; no access log.
- `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full` (not `strict`), `ProtectHome`.

**qcluster unit:** the same sandbox.

**Backups:** a backup service (runs as root, no `OnFailure`) and a timer at 02:30.

**Nginx**
- TLS 1.2 and 1.3; an HTTP→HTTPS redirect.
- Rate limits: `gc_login` 10 r/m and `gc_api` 20 r/s.
- Body size: 60 MB by default, 200 MB for citizen posts, 70 MB for admin ads.
- Feed micro-cache, bypassed when the request carries an Authorization header or a session cookie.
- `/media/`: CSP sandbox and nosniff.
- `/django-admin/`: an allow line per `OFFICE_IPS` entry, then deny all.
- CSP allows `'unsafe-eval'` (accepted risk).
- Nested locations lose the security headers, because nginx does not inherit `add_header` into a block that sets its own.
- No `server_tokens off` and no `Permissions-Policy` (`nginx-locations.conf`, `nginx-security.conf`).

**Host**
- ufw: deny incoming by default; allow SSH, 80 and 443.
- fail2ban: `sshd` and `nginx-limit-req` jails.
- SSH: keys only, no root login.
- Unattended security upgrades.
- Certbot on webroot, with a reload hook.

**Secrets:** `/etc/gramaconnect` with modes 750, 640 and 600. `SECRET_KEY` is generated on the server.

**Backups (`backup.sh`)**
- A DB dump plus media, encrypted with gpg AES256, with checksums.
- The off-site target defaults to the placeholder `[BACKUP TARGET]` (`lib.sh:52`), so by default the backup stays on the server.
- `/etc/gramaconnect` is not backed up.
- Restore is a manual drill.

**Sudo:** the default `SUDO_NOPASSWD=1` gives the deploy user `NOPASSWD:ALL` (`setup.sh:109-120`, `lib.sh:49`).

**`deploy.sh`**
- It runs `git merge` and `pip install` into the live venv (:50-65) **before** `check --deploy` (:74) and pip-audit (:83-90).
- The comment at :70 says a failed check leaves the running version unchanged. That is not true.

### B. Panel path (`deploy/qa/`)

`deploy/qa/README.md:178-182` says this path runs **qa and production (www.gramaconnect.com)**. (My notes record that a separate production team deploys production; we only validate it.)

**qa:** Django `runserver 0.0.0.0:$PORT` with `DEBUG=False` (`BVT-start.sh:95`).

**Production**
- Gunicorn on 127.0.0.1, 3 workers, `--timeout 300` (:91-93).
- The `.env` (`BVT-deploy.sh:209-235`, written with umask 077) sets `SERVE_FILES=True` and `CITIZEN_INTEGRITY_REQUIRED=False`.
- `check --deploy` runs before migrate.
- `SEED` and `QA_SQLITE` are refused.
- The panel's nginx (TLS, headers, `/django-admin/` allowlist, rate limits, body size) is **not in the repo**.
- The `setup.sh`-path hardening (systemd sandbox, ufw, fail2ban, nginx limits) **does not apply here**.

**Backups:** `BVT-backup.sh` writes a `dumpdata` JSON gzip and a media tar, **not encrypted**, kept as the last 7 in `~/bvt-backups` on the same host, with **no off-site copy** (`BVT-backup.sh:12-61`). The dump holds password hashes and personal data.

**Settings-module marker:** `DJANGO_SETTINGS_MODULE=grama.settings.prod` still works. Importing it resolves through the `sys.modules` alias (the agent checked this with an import). The scripts use it only as a production marker (`qa-lib.sh:29-35`).

### Load test

`deploy/loadtest/locustfile.py` has two users:
- A citizen walks the geo cascade, then reads the feed with an ETag, the next page, post details and contacts.
- An official sends wrong-password logins and expects 400 or 429.

There is no load test of the review queue, posting, uploads or the console.

---

## 10. CI and security scanning

`.github/workflows/security.yml`:

| Item | Value |
|---|---|
| Triggers | pull requests into `dev`, pushes to `dev`, weekly cron (Monday 02:30 UTC), manual dispatch. **Nothing for `qa` or `main`.** |
| Permissions | `contents: read` (top level) |
| Runner and Python | ubuntu-24.04, Python 3.12 |
| Bandit 1.9.4 | `-r apps grama -x tests,migrations --severity-level medium` |
| Semgrep 1.183.0 | `p/python p/django p/secrets`, severity WARNING and ERROR, `--error`, on `apps grama deploy` |
| pip-audit 2.10.1 | on `requirements.txt` |
| Blocking | Each job fails on findings, but they become merge gates only once branch protection makes them required checks. `docs/security-review.md:47` lists that as **open**. |
| Action pinning | By tag (`actions/checkout@v7`, `setup-python@v7`), not by SHA. Tool versions are pinned. |
| **Test suite** | **Not run in CI.** Neither is `makemigrations --check`. |
| Config files | No `.bandit` or `.semgrep.yml`; only command-line flags. |

**Dependabot** (`.github/dependabot.yml`): pip weekly and GitHub Actions monthly, both into `dev`; semver-major updates ignored; at most 5 open pull requests.

**Not verified:**
- The current CI status and any findings on GitHub (I had no access to the Actions runs).
- Local pip-audit, Bandit and Semgrep results (not installed here).

---

## 11. Test inventory

**Counts.** `pytest --collect-only -q` collects **2,869 tests in 1.08 s**. Grep finds 60 test files and about 906 test functions before parametrisation.

| Area | Collected | Area | Collected |
|---|---|---|---|
| accounts | 1,651 | ads | 68 |
| posts | 227 | notifications | 69 |
| contacts | 204 | share | 50 |
| geography | 150 | feed | 40 |
| citizens | 87 | bills | 39 |
| service_requests | 86 | web_public | 32 |
| web_admin | 82 | audit | **7** |
| grama/tests | 77 | | |

**Configuration**
- `pytest.ini`: `DJANGO_SETTINGS_MODULE=grama.settings`, `testpaths = apps grama`, `-ra --strict-markers`.

**Fixtures**
- Root `conftest.py` (216 lines):
  - A hand-built map: 1 state, 2 districts, 4 mandals, 8 villages, with Telugu names.
  - 14 officials, one per role and area.
  - `geo`, `world`, `api` (JWT) and `console` (session) fixtures, plus `super_admin_can_post`.
  - Helpers that work out expected answers independently (`inside`, `ancestors`).
  - The throttle cache is cleared automatically before each test.
- App conftests in citizens, contacts, feed, notifications (`push_outbox`), posts (tmp `media_root`), service_requests and web_public.
- **No factory library** (no factory_boy or model_bakery).

**What changes under tests** (`grama/settings.py`):
- Detection: `pytest` in `sys.modules` (:30).
- SQLite is forced in the Dev branch only (:93); see BE-TEST-001.
- `.test-media/` (:115).
- MD5 hasher, locmem email and push, transliteration off, retry base 0 (:171-176).
- No browsable renderer (:355).
- `Q_CLUSTER sync` (:395).

**Gaps**
- `audit` has only 7 tests, and none checks that personal data stays out of `data` (BE-SEC-001 went undetected).
- Tests are not run in CI.
- I did not run the suite itself (collect only), so whether it passes is **not verified** in this stage.

---

## 12. Large files (over 400 lines, measured with `wc -l`)

| File | Lines | Kind |
|---|---|---|
| apps/posts/services.py | 765 | services (state machine) |
| apps/accounts/tests/test_auth.py | 677 | test |
| grama/settings.py | 557 | settings |
| apps/contacts/services.py | 547 | services |
| apps/accounts/authz.py | 522 | authz |
| apps/geography/importer.py | 519 | CSV import |
| apps/service_requests/tests/test_service_requests.py | 512 | test |
| apps/posts/views.py | 477 | views |
| apps/share/tests/test_share.py | 467 | test |
| apps/accounts/views_users.py | 465 | views |
| apps/accounts/serializers.py | 461 | serializers |
| apps/web_admin/api.py | 452 | views |
| apps/accounts/tests/test_user_permissions.py | 442 | test |
| apps/posts/tests/test_owner_decisions.py | 421 | test |

**Largest views:** `PostViewSet` (`posts/views.py:172-353`, about 180 lines, 15 actions) and `UserViewSet` (`accounts/views_users.py:124-365`).

**Largest serializers:** `PostSerializer` (`posts/serializers.py:165-270`, 13 method fields) and `MeSerializer` (`accounts/serializers.py:165-257`).

---

## 13. Findings

Findings are listed by severity, then by ID.

### High

**BE-SEC-001: Full email and mobile number written to the audit log and returned by the audit API**
- **Severity:** High
- **Evidence:**
  - `apps/accounts/views_users.py:293` calls `record("user.updated", …, data=dict(data.validated_data))`.
  - `UserUpdateSerializer.Meta.fields` includes `email` and `mobile` (`apps/accounts/serializers.py:416`).
  - `record()` does not mask (`apps/audit/services.py:37-50`).
  - `AuditEntrySerializer` returns `data` (`apps/audit/views.py:49`), and the CSV export includes it.
- **Impact:**
  - Breaks the CLAUDE.md non-negotiable "never log or audit full emails and mobile numbers".
  - The audit table is never cleaned and refuses deletes, so the data cannot be removed later.
  - It is visible to every Super Admin and in exports.
- **Recommendation:**
  - Record only which fields changed, with masked values for contact details.
  - Add an audit test that asserts no full email or phone number appears in `data`.
  - Decide how to clean the rows already written; this needs an owner decision because the audit log is immutable.
- **Effort:** S

**BE-SEC-002: Citizen "same phone" linking trusts a client-supplied, unattested `android_id`**
- **Severity:** High
- **Evidence:**
  - `apps/citizens/services.py:101-102, 116-121, 190`: the hash of whatever `android_id` the client sends.
  - Blocks and daily limits apply through `same_phone` (`citizens/services.py:124-133, 241, 254`).
  - `apps/service_requests/views.py:72` returns every service request (name, phone, email, address) across `same_phone`.
  - Registration is not rate-limited by default (`settings.py:478`, `services.py:160-161`), and Play Integrity is off on production (`deploy/qa/BVT-deploy.sh:232`).
- **Impact:**
  - A blocked or rate-limited citizen gets a fresh, unlimited identity by sending a blank or new `android_id`.
  - Anyone who learns another phone's ANDROID_ID can read that phone's service-request personal data and can get that phone blocked along with their own. ANDROID_ID differs per app signing key on Android 8+, which makes this harder but possible with device access.
- **Recommendation:**
  - Do not use an unattested client value as an identity or authorisation link. Scope service-request reads to the token's own device, or prove "same phone" with something attested.
  - Turn on a default registration limit per IP.
  - Treat block evasion as an accepted risk until Play Integrity is on, and record that with the owner.
- **Effort:** M

### Medium

**BE-SEC-003: Production runs on the panel path, outside the hardening kept in the repo**
- **Severity:** Medium
- **Evidence:**
  - `deploy/qa/README.md:178-182` (production on the panel) contradicts `:152-155` ("production uses `deploy/setup.sh`").
  - The panel nginx config is not in the repo.
  - `/django-admin/` has no check in the app (`grama/urls.py:47`); it relies on nginx `OFFICE_IPS`.
  - The nginx rate limits, systemd sandbox, ufw and fail2ban exist only on the `setup.sh` path.
- **Impact:** the reviewed controls (admin IP allowlist, `/api/` rate limits, body-size caps, security headers) may not exist on production. This could not be verified.
- **Recommendation:**
  - Keep the production web-server configuration in the repo, or record it, and check it against the `setup.sh` templates.
  - Add an app-level guard for `/django-admin/`, such as an IP allowlist setting, so it does not depend on the proxy.
  - Fix the contradictory README.
- **Effort:** M

**BE-SEC-004: Production backups are unencrypted and stay on the same host**
- **Severity:** Medium
- **Evidence:** `deploy/qa/BVT-backup.sh:12, 19, 48-61` writes a `dumpdata` JSON (password hashes, personal data) and a media tar, keeps 7 copies in `~/bvt-backups`, with no gpg and no off-site copy. `qa/README.md:239` says production uses it.
- **Impact:** losing the host loses the data and its backups together, and a host compromise exposes every backup in clear.
- **Recommendation:** encrypt, copy off the host, and run a restore drill, matching what `backup.sh` already does on the root path.
- **Effort:** M

**BE-SEC-005: An anonymous device update can delete any other device row with a given FCM token**
- **Severity:** Medium
- **Evidence:**
  - `apps/notifications/views.py:60, 70-77`: `AllowAny`; `Device.objects.filter(fcm_token=token).exclude(pk=device.pk).delete()`.
  - `devices/` POST is anonymous and unthrottled (:35).
- **Impact:** a caller holding any unlinked device id plus another phone's FCM token, including an official's, removes that registration, and push to that phone silently stops.
- **Recommendation:**
  - Only replace a token row that is unlinked or belongs to the same user.
  - Throttle device registration.
- **Effort:** S

**BE-SEC-006: No application-level throttling on anonymous endpoints; unlimited citizen registrations by default**
- **Severity:** Medium
- **Evidence:**
  - No default throttle classes (`settings.py:330-353`).
  - No throttle on `citizen/challenge/`, `citizen/register/`, `devices/`, `auth/refresh/`, `feed/`, `geo/villages/?q=` (an unindexed `icontains`), `geo/nearest/` or `/p/{code}/`.
  - `CITIZEN_REGISTRATIONS_PER_IP_PER_DAY=0` means no limit (`settings.py:478`; `citizens/services.py:161`).
- **Impact:** scraping, token minting and cheap database-heavy requests, with protection only from nginx `gc_api` on the `setup.sh` path. That protection is unknown on production (BE-SEC-003).
- **Recommendation:** add scoped DRF throttles (anonymous and citizen) on a shared cache, and set a non-zero default for registrations per IP.
- **Effort:** S–M

**BE-SEC-007: The failed-login audit stores whatever was typed in the username box**
- **Severity:** Medium
- **Evidence:** `apps/accounts/lockout.py:125-132`. Usernames may be email-shaped (`models.py:20`).
- **Impact:** a password typed into the username box, or a full email address, ends up permanently in the audit log, against the privacy rules.
- **Recommendation:** for unknown accounts, record a masked or hashed form of the username.
- **Effort:** S

**BE-SEC-008: Django admin changes skip the audit log and token revocation**
- **Severity:** Medium
- **Evidence:**
  - `apps/accounts/admin.py:78-84`: UserAdmin can edit password, `is_active` and `is_superuser`.
  - `admin.py:97-108`: RoleAssignment saves.
  - Only changes to a group's permissions are audited (`signals.py:77-103`).
  - `ServiceRequestAdmin` can edit citizen personal data, also unaudited.
- **Impact:** a password changed or a user deactivated through `/django-admin/` does not bump `token_version`, so old JWTs and sessions keep working. Not verified at runtime.
- **Recommendation:** make the sensitive admin forms read-only, or route their saves through the same services (audit plus revoke).
- **Effort:** M

**BE-COR-001: Invalid UUIDs in query parameters likely return 500**
- **Severity:** Medium
- **Evidence:** `apps/audit/views.py:85-86` (`?actor=`), `apps/posts/views.py:193` (`?author=`), and the media-delete route regex `[0-9a-f-]+` (`posts/views.py:348-351`). Django's `ValidationError` is not mapped (`grama/exceptions.py:85-118`).
- **Impact:** 500 responses with stack traces in the logs, against the contract's error shape. Not verified at runtime.
- **Recommendation:** validate UUID parameters in the views, or map core `ValidationError` to 400 `validation_error`.
- **Effort:** S

**BE-COR-002: Count-then-insert limits and exists-then-save uniqueness can be beaten by concurrent requests**
- **Severity:** Medium
- **Evidence:**
  - Citizen daily posts: `citizens/services.py:258-264`.
  - Service requests: `service_requests/services.py:205-212`.
  - Contact messages: `web_public/api.py:121-131` (no limit at all when the IP is unknown).
  - Username and mobile on user creation: `views_users.py:255-273` (IntegrityError not caught).
  - Service and bill keys: `service_requests/services.py:107-112`, `bills/services.py:32-37`.
  - Contacts have no database UQ (`contacts/models.py:106-110`).
- **Impact:** limits can be exceeded, and some concurrent duplicates return a 500 instead of a 409.
- **Recommendation:**
  - Catch `IntegrityError` and return the contract's 409.
  - Back the uniqueness rules with constraints.
  - Accept or harden the soft limits.
- **Effort:** M

**BE-PERF-001: Notification fan-out runs for each recipient inside the request**
- **Severity:** Medium
- **Evidence:** `apps/notifications/services.py:118-143`, where each recipient costs a template query, a new AreaDescriber, a Notification insert and an `async_task` insert. The same pattern is in `tell_super_admins` (:225-245) and `notify_service_request` (:286-300). It is triggered on commit by every submit, review and escalation.
- **Impact:** submit and review latency grows with the number of reviewers in the area, which matters on 2G clients.
- **Recommendation:** move the fan-out into one background task per event, and bulk-create the notifications.
- **Effort:** M

**BE-PERF-002: Reports and dashboard run per-area queries and paginate in Python**
- **Severity:** Medium
- **Evidence:** `apps/web_admin/api.py:101-108` (every post status loaded into Python), :127-139 (one query per district), :209-219 (one per district and per mandal), :274-312 and :331 (admin-activity built in full, then sliced).
- **Impact:** the number of queries grows with the 26 districts and about 680 mandals. The query count was not measured at real data size.
- **Recommendation:** aggregate with GROUP BY in SQL, paginate in the database, and profile with production-scale data in Stage 3.
- **Effort:** M

**BE-PERF-003: Media processing is synchronous and citizen posts have no file-count limit**
- **Severity:** Medium
- **Evidence:**
  - `posts/media.py:182-230`: ffprobe and ffmpeg run in the request, with timeouts up to 120 s.
  - `CITIZEN_MAX_PHOTOS=0` and no video cap (`settings.py:477`; `citizens/services.py:265-271`).
  - Gunicorn `--timeout 300` for every request (`templates/gramaconnect-gunicorn.service:21`; `BVT-start.sh:91-93`).
  - nginx allows 200 MB on `citizen/posts/`.
  - The panel has 3 sync workers.
- **Impact:** a few anonymous-token uploads can hold every worker for minutes.
- **Recommendation:**
  - Cap files per citizen post.
  - Move video work to the job worker, or limit concurrency.
  - Use a short default Gunicorn timeout, with longer ones only on import and upload routes.
- **Effort:** M

**BE-CI-001: CI does not run the tests or the migration check, and the scans are not merge gates**
- **Severity:** Medium
- **Evidence:** `.github/workflows/security.yml` has only Bandit, Semgrep and pip-audit, and runs only on `dev`. `docs/security-review.md:47` lists required checks as open (G10). Actions are pinned by tag.
- **Impact:** "every pull request keeps the whole suite green" (conventions.md) is not enforced. Regressions, BE-SEC-001 included, can merge unnoticed.
- **Recommendation:**
  - Add a pytest job and `makemigrations --check` on pull requests.
  - Make all jobs required checks.
  - Pin actions by SHA.
- **Effort:** S

**BE-TEST-001: Tests run on SQLite only while DEBUG is on**
- **Severity:** Medium
- **Evidence:** `grama/settings.py:89-93`: with `DEBUG=False` in the environment or `.env`, pytest uses `DATABASE_URL` and the production `MEDIA_ROOT` (:111). No conftest guard.
- **Impact:** running pytest on a server checkout would create test databases on the real database server and write files into real media folders. This contradicts the "tests always SQLite" decision.
- **Recommendation:** force SQLite and the test media folder whenever tests are detected, whatever `DEBUG` is.
- **Effort:** S

**BE-DOC-001: Documented stack and defaults do not match the code**
- **Severity:** Medium
- **Evidence:**
  - The brief and `.claude/project.md` list "settings files base, dev, prod, test" and `SUPER_ADMIN_CAN_POST=False`. The code has one file (`grama/settings.py`), and `SUPER_ADMIN_CAN_POST` defaults to True (:406, owner decision of 2026-11-04 per `docs/authorization.md:410`).
  - `.claude/architecture.md:37, 64` say only urgent posts push at high priority; `PUSH_ALL_POSTS_HIGH_PRIORITY=True` (:518).
  - `architecture.md:46` says tests are "always SQLite" (see BE-TEST-001) and that production has "Play Integrity on" (it is off on the panel).
- **Impact:** client teams and later audit stages work from wrong defaults. `project.md` is shared by all three repos.
- **Recommendation:** update `project.md` in all three repos together, and update `architecture.md` and the stack brief.
- **Effort:** S

### Low

**BE-SEC-009: DEBUG fails open**
- **Severity:** Low
- **Evidence:**
  - `grama/settings.py:27` defaults to True.
  - `deploy.sh` never writes `DEBUG`.
  - `qa/README.md:192-194` documents that an older production `.env` would come up in debug mode on SQLite.
  - Mitigated by `check --deploy --fail-level WARNING` (`deploy.sh:74`).
- **Impact:** a missing line in a server `.env` exposes debug pages.
- **Recommendation:** default to production unless development is explicitly asked for, or refuse to start without an explicit `DEBUG` on non-local hosts.
- **Effort:** S

**BE-SEC-010: Test mode is detected from `sys.modules`, and test tools are installed in production**
- **Severity:** Low
- **Evidence:** `settings.py:30` (`"pytest" in sys.modules`) switches to the MD5 hasher and the locmem push gateway. `requirements.txt:31-36` installs pytest, pytest-django and Locust everywhere.
- **Impact:** an import of pytest in production would silently weaken hashing and disable push. It also widens the attack surface.
- **Recommendation:** detect tests explicitly (an environment flag set by pytest configuration), and keep the single-file rule with test tools behind an extra.
- **Effort:** S

**BE-SEC-011: `deploy.sh` updates code and packages before its safety checks**
- **Severity:** Low
- **Evidence:** `deploy/deploy.sh:50-65` merges and installs into the live venv, then checks at :70-90. The comment at :70 is wrong. No `--require-hashes`.
- **Impact:** a failed check still leaves new code in place, and workers recycled by `max-requests` pick it up.
- **Recommendation:** build into a new release directory or venv, check it, then switch.
- **Effort:** M

**BE-SEC-012: The deploy user gets `NOPASSWD:ALL` by default**
- **Severity:** Low
- **Evidence:** `deploy/setup.sh:109-120`, `lib.sh:49`.
- **Impact:** compromising the deploy account means compromising root.
- **Recommendation:** make the restricted sudo rule the default.
- **Effort:** S

**BE-SEC-013: Personal data in query strings**
- **Severity:** Low
- **Evidence:** user search over email and mobile (`accounts/views_users.py:176-184`); service-request search by phone (`service_requests/views.py:115-121`).
- **Impact:** phone numbers and emails can end up in proxy logs. nginx on the `setup.sh` path logs without query strings; the panel is unknown.
- **Recommendation:** send search terms in a POST body, or confirm that the logs strip query strings everywhere.
- **Effort:** S

**BE-SEC-014: Partial Play Integrity verdict check**
- **Severity:** Low
- **Evidence:** `citizens/integrity.py:112-118`: the licensing verdict and certificate digest are not checked. The credentials file is re-read on every call (:84-86).
- **Impact:** a re-signed or sideloaded app may pass once integrity is turned on.
- **Recommendation:** check `appLicensingVerdict` and the certificate digests against `ANDROID_CERT_SHA256`.
- **Effort:** S

**BE-SEC-015: The default cache is per process in production**
- **Severity:** Low
- **Evidence:** `settings.py:155`: LocMem is the default cache. The transliterate throttle and its result cache use it.
- **Impact:** the effective limit is 60/min multiplied by the number of workers, and the cache is not shared.
- **Recommendation:** use the database cache (or a shared one) for throttles.
- **Effort:** S

**BE-SEC-016: Object permission returns 403, not 404 as the convention says**
- **Severity:** Low
- **Evidence:** `accounts/permissions.py:54-57`.
- **Impact:** it would reveal that an object exists if a view ever relies on it. No current view in scope does.
- **Recommendation:** align the code with the convention, or document the exception.
- **Effort:** S

**BE-COR-003: Deleting a draft leaves its video file on disk**
- **Severity:** Low
- **Evidence:** `apps/posts/services.py:558-561` deletes the image, medium and thumbnail files, but not `item.video`.
- **Impact:** orphaned files of up to 50 MB each.
- **Recommendation:** delete every file field, and add a sweep for orphans.
- **Effort:** S

**BE-PERF-004: The contacts CSV import runs several queries per row**
- **Severity:** Low
- **Evidence:** `apps/contacts/services.py:476-544`: about 4–7 queries per row, up to 5,000 rows, in one request.
- **Impact:** tens of thousands of queries in a single request, inside the 300 s timeout.
- **Recommendation:** load the lookups in bulk first, and write with bulk operations.
- **Effort:** M

**BE-PERF-005: Push retries sleep in the worker and open a new FCM session on every task**
- **Severity:** Low
- **Evidence:** `grama/retry.py:47-48`; `notifications/push.py:167`.
- **Impact:** with only 2 workers, a slow FCM blocks other jobs.
- **Recommendation:** retry through django-q scheduling, and reuse the authorised session.
- **Effort:** S

**BE-PERF-006: The geography tree loads everything**
- **Severity:** Low
- **Evidence:** `geography/views_admin.py:197-227`: village counts done in Python; `?villages=1` serialises every village (about 18k rows).
- **Impact:** a heavy console request.
- **Recommendation:** count with `annotate`, and load villages lazily per mandal.
- **Effort:** S

**BE-PERF-007: Search uses unindexed `icontains`**
- **Severity:** Low
- **Evidence:** audit `q` on 4 columns (`audit/views.py:78-84`), posts `q` (`posts/views.py:202`), feed `q` (`feed/views.py:141`), and the public villages `q` (`geography/views.py:117`, unthrottled).
- **Impact:** full scans that grow with the data. The audit log is never cleaned.
- **Recommendation:** prefix search on normalised keys, or a search index. Measure in Stage 3.
- **Effort:** M

**BE-OPS-001: Backup gaps on the root-server path**
- **Severity:** Low
- **Evidence:** the off-site target defaults to a placeholder (`lib.sh:52`); `/etc/gramaconnect` is not backed up; no automated restore test (RUNBOOK:91-100); the backup unit has no `OnFailure` alert.
- **Impact:** backups may sit only on the server, and a failed backup goes unnoticed.
- **Recommendation:** require a real target before setup completes, back up the secrets separately, and alert on failure.
- **Effort:** S

**BE-OPS-002: Hardening gaps in systemd and nginx**
- **Severity:** Low
- **Evidence:**
  - systemd: `ProtectSystem=full`, not `strict`, so `/srv` (code and venv) stays writable.
  - nginx: nested `location` blocks that set their own `add_header` drop CSP, X-Frame-Options and HSTS (`nginx-locations.conf:138-178`); no `server_tokens off`; no `Permissions-Policy`.
- **Impact:** defence-in-depth gaps.
- **Recommendation:** tighten the sandbox, and repeat or include the security headers in the nested blocks.
- **Effort:** S

**BE-OPS-003: Ubuntu 22.04 is claimed as supported, but it ships Python 3.10**
- **Severity:** Low
- **Evidence:** `setup.sh:2, 38-40` and `deploy/README.md:3`, against the 3.12 minimum (`requirements.txt:2`).
- **Impact:** setup could fail, or run on an untested interpreter.
- **Recommendation:** check the Python version in `setup.sh`, or drop 22.04 from the docs.
- **Effort:** S

**BE-API-001: OpenAPI schema quality**
- **Severity:** Low
- **Evidence:** measured with `spectacular --validate`:
  - 4 views have no response schema (Dashboard, Report, AppConfig, Stats).
  - The `PatchedDeviceUpdateRequest` component name collides.
  - The posts `id` path parameter is untyped.
  - 13 enum naming collisions.
- **Impact:** client code generated from Swagger will be wrong or missing for these endpoints.
- **Recommendation:** declare the serializers or schemas, rename one of the two `DeviceUpdateSerializer`s, and add `ENUM_NAME_OVERRIDES`.
- **Effort:** S

**BE-API-002: Contract drift**
- **Severity:** Low
- **Evidence:**
  - `docs/api-contract.md:811` names `GET /geo/villages/nearest`, which does not exist (the real path is `geo/nearest/`).
  - `:813` names `GET /feed/summary`, which does not exist.
  - `health/` is not documented.
  - `.claude/api.md` does not list `geo/admin/villages/`.
- **Impact:** client teams may code against endpoints that do not exist.
- **Recommendation:** fix the contract and the client table, and add a contract-vs-schema check.
- **Effort:** S

**BE-CFG-001: Upload memory limits rely on defaults, and the geography import has no size cap**
- **Severity:** Low
- **Evidence:** `DATA_UPLOAD_MAX_MEMORY_SIZE` and `FILE_UPLOAD_MAX_MEMORY_SIZE` are not set. `geography/views_admin.py:264` reads the whole upload into memory, with no size check, unlike the contacts import.
- **Impact:** memory pressure from a large upload (Super Admin only).
- **Recommendation:** add an explicit size cap like the contacts import has.
- **Effort:** S

**BE-DOC-002: Deploy docs contradict themselves or are out of date**
- **Severity:** Low
- **Evidence:**
  - `deploy/qa/README.md:152-155` vs :178-182 on how production is deployed.
  - RUNBOOK:70 names `config.env`; the code uses `deploy.env` (`lib.sh:15`).
  - RUNBOOK:64 says the web repo is built on the server; `deploy.sh:109-112` uses the committed `dist/`.
  - `.claude/backlog.md` still lists video metadata as open; it was closed on 2026-11-03.
  - The qcluster unit comment still mentions emails.
  - `docs/security-review.md:35` has a typo.
- **Impact:** operator mistakes.
- **Recommendation:** fix the docs.
- **Effort:** S

**BE-TEST-002: The load test covers only citizen reads and failed logins**
- **Severity:** Low
- **Evidence:** `deploy/loadtest/locustfile.py:37-125`.
- **Impact:** no data on posting, review, uploads or the console under load.
- **Recommendation:** add those scenarios before Stage 3 performance work.
- **Effort:** M

### Info

**BE-CFG-002: Local venv drift**
- **Severity:** Info
- **Evidence:** `pip list` shows django-otp 1.7.5, which is not in requirements; pytest 9.1.1 instead of 9.2.0; Locust 2.46.7 instead of 2.47.0.
- **Impact:** local results may differ from CI and the servers.
- **Recommendation:** run `pip install -r requirements.txt` in a clean venv.
- **Effort:** S

**BE-CFG-003: The local dev database is behind**
- **Severity:** Info
- **Evidence:** `showmigrations` shows 4 unapplied migrations: accounts 0009, notifications 0011 and 0012, posts 0009.
- **Impact:** only the local runserver is affected.
- **Recommendation:** migrate locally (the developer's own action).
- **Effort:** S

**BE-API-003: The schema command crashes on a Windows console**
- **Severity:** Info
- **Evidence:** `UnicodeEncodeError` (cp1252) unless `PYTHONIOENCODING=utf-8` is set.
- **Impact:** developers on Windows cannot export the schema.
- **Recommendation:** document the setting, or use `--file`.
- **Effort:** S

**BE-SEC-017: The qa server exposes Django's development server**
- **Severity:** Info
- **Evidence:** `deploy/qa/BVT-start.sh:95` runs `runserver 0.0.0.0` with `DEBUG=False`. This is a documented qa choice.
- **Impact:** the development server is not meant for public networks.
- **Recommendation:** run qa on Gunicorn too, as production does.
- **Effort:** S

**BE-PERF-008: Probable per-row permission queries in the officials' service-request list**
- **Severity:** Info
- **Evidence:** `service_requests/serializers.py:188` and `services.py:71` call `authz` for each assignee object. Not measured.
- **Impact:** possible N+1 queries.
- **Recommendation:** measure the query count in Stage 3.
- **Effort:** S

### Done well

- **Post state machine:** every move is one conditional UPDATE, with the audit entry and ReviewAction in the same transaction and the signals sent after commit. No status writes exist outside `services.py` (grep).
- **Authorisation:** permission and area checks are centralised in `authz`. There are no group-name checks. Lists are filtered in SQL, and objects outside reach answer 404 in the views.
- **Revocation and lockout:** `token_version` works for JWTs and sessions, and refresh tokens are blacklisted in bulk. Lockout is layered: axes, an IP-wide cap, a login throttle and a wrong-password counter. Unknown usernames take the same time as real ones.
- **Privacy:** contacts mask phone numbers in the audit log; citizen IPs and ANDROID_IDs are keyed hashes; the citizen token is stored only as a hash; nonces are HMAC-signed, single-use and compared in constant time.
- **Uploads and exports:** Pillow re-encodes images safely; ffmpeg runs without a shell; CSV exports are formula-safe and start with a BOM; the `/media/` CSP sandbox; the share page has a strict CSP.
- **Database design:** indexes match the main filters (feed, review queue, targets, audit). Constraints back the rules (sibling names, post targets, event dates, author or citizen). The ORM is portable, with no raw SQL.
- **Tests:** 2,869 tests, permission matrices whose expected answers are worked out independently, and Telugu fixtures throughout.
- **Root-server deploy path:** strict shell mode, validated identifiers, secrets generated with tight file modes, encrypted backups with checksums, and nginx logs without query strings.

---

## 14. Audit area ratings

| # | Area | Rating | Justification |
|---|---|---|---|
| 1 | Settings and configuration | 7/10 | Strict production checks and validated settings, but DEBUG fails open, test detection is fragile, the cache is per process, and upload limits rely on defaults. |
| 2 | Authentication and authorisation | 8/10 | Central `authz`, permission plus area checks, token versions, layered lockout. Let down by gaps in the Django admin and the 403-vs-404 convention. |
| 3 | Data model and database | 8/10 | Good indexes and constraints, portable ORM, conditional updates. Some uniqueness rules have no database backing. |
| 4 | API design, contract and schema | 7/10 | Consistent error shape and pagination. Schema errors and collisions, and the contract names endpoints that do not exist. |
| 5 | Privacy and personal data | 6/10 | Strong hashing and masking in most places, but full email and mobile in the audit log, raw typed usernames, and personal data in query strings. |
| 6 | Citizen abuse controls | 5/10 | Good token and nonce design, undermined by the unattested `android_id`, unlimited registrations, no file-count cap, and integrity off on production. |
| 7 | Performance and scalability | 6/10 | The feed and post lists are well optimised. Fan-out in the request, per-area report queries, and synchronous media processing. |
| 8 | Background jobs and integrations | 7/10 | Clean signals and on-commit design, timeouts on every call, no secrets logged. Retries sleep in a 2-worker pool. |
| 9 | Deployment and operations | 6/10 | The root-server path is well hardened, but production runs on the panel path with unencrypted on-host backups and its proxy config outside the repo. |
| 10 | CI and supply chain | 4/10 | Pinned packages and three scanners, but no tests in CI, no required checks, and actions pinned by tag. |
| 11 | Tests | 8/10 | Large, matrix-based suite with Telugu fixtures. Thin audit tests, and the SQLite guarantee depends on DEBUG. |
| 12 | Documentation | 6/10 | Extensive, but drift in project-level defaults, the stack description and the deploy docs. |
| | **Overall** | **6.5/10** | Mean of the 12 areas. |

---

## 15. Audit map: ten areas most likely to hold risk (to guide Stages 2 and 3)

1. **Citizen identity and abuse** (`apps/citizens/services.py`, `integrity.py`, `service_requests/views.py:72`). Covers `same_phone`, registration limits, block evasion, and media volume per post. *Stage 2, security.*
2. **Personal data in the audit log and logs** (`audit/services.py`, every `record(..., data=…)` call, `lockout.py:125-132`). Every audit call site should be checked for raw personal data. *Stage 2.*
3. **Production as actually deployed** (`deploy/qa/*`, the panel nginx). Headers, `/django-admin/` reachability, rate limits, body size, backups and `TRUST_X_REAL_IP`, checked read-only against the live site. *Stage 2, with the production team.*
4. **Anonymous endpoint surface** (feed, geo, devices, citizen challenge and register, share, refresh). Throttling and the cost per request. *Stage 2 and 3.*
5. **Django admin as a side door** (`accounts/admin.py`, `service_requests/admin.py`). Audit, token revocation, and fields that should be read-only. *Stage 2.*
6. **Request-path work** (`notifications/services.py:118-143`, `posts/media.py`, `web_admin/api.py` reports, the contacts import). Query counts and latency at production data size. *Stage 3, performance.*
7. **Concurrency and integrity** (count-then-insert limits, `exists()`-then-save, IntegrityError handling, invalid-UUID 500s). *Stage 2, correctness.*
8. **CI and the supply chain** (`security.yml`, branch protection, action pinning, test tools in production requirements). *Stage 2.*
9. **API contract vs implementation** (`docs/api-contract.md` against the 128 operations, schema errors, `allowed_actions`). *Stage 3.*
10. **Settings fail-safety** (DEBUG default, test detection, cache backends, upload limits, the SQLite guarantee under tests). *Stage 2.*

---

## 16. Not verified (and why)

- **pip-audit, Bandit and Semgrep results:** not installed locally, and installing them was out of bounds. CI status on GitHub was not accessible.
- **Test pass/fail:** only `--collect-only` was run, as the brief allowed.
- **Panel production nginx, TLS, headers, `/django-admin/` reachability, real client IP:** the configuration is not in the repo and the live servers were out of scope.
- **Runtime behaviour** of BE-COR-001 (500s on invalid UUIDs), BE-SEC-008 (admin password change keeps tokens alive), ffmpeg `TimeoutExpired` handling, and the per-row authz N+1 queries: inferred from reading the code, not executed.
- **Query counts and latency:** nothing was measured. No metrics in this report are estimates, apart from the row counts quoted from the docs (about 18k villages).
- **Whether `ensure_schedules` has run on each server** (the escalation and housekeeping schedules).
- **Python 3.12-only syntax:** a quick grep found none. Full compatibility with 3.10 was not checked.

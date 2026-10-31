"""Settings shared by every environment.

dev.py, prod.py and test.py import everything from here and change only what
differs. Values that differ between machines come from environment variables,
read with django-environ. A .env file in the repo root is read too when it
exists; real environment variables win over it.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
_ENV_FILE = BASE_DIR / ".env"
if _ENV_FILE.exists():
    environ.Env.read_env(str(_ENV_FILE), overwrite=False)


def database_config(url: str) -> dict:
    """Turn a DATABASE_URL into Django's DATABASES["default"].

    Only MySQL gets extra OPTIONS: utf8mb4 so Telugu is stored correctly, and
    strict mode so bad data fails loudly instead of being truncated. Other
    engines are used exactly as parsed.
    """
    config = environ.Env.db_url_config(url)
    if config["ENGINE"] == "django.db.backends.mysql":
        options = config.setdefault("OPTIONS", {})
        options.setdefault("charset", "utf8mb4")
        options.setdefault("init_command", "SET sql_mode='STRICT_TRANS_TABLES'")
        config.setdefault("TEST", {}).update(
            {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"}
        )
    config.setdefault("CONN_MAX_AGE", env.int("DB_CONN_MAX_AGE", default=60))
    config.setdefault("CONN_HEALTH_CHECKS", True)
    return config


# --- Applications -----------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    # Failed-login lockout (apps/accounts/lockout.py).
    "axes",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_q",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.geography",
    "apps.posts",
    "apps.feed",
    "apps.notifications",
    "apps.audit",
    "apps.web_public",
    "apps.web_admin",
    "apps.contacts",
    "apps.citizens",
    "apps.service_requests",
    "apps.ads",
    "apps.bills",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # ETag on GET responses and 304 Not Modified for repeat requests: saves data on 2G.
    "django.middleware.http.ConditionalGetMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must stay last: turns axes lockouts on other login forms into 429 responses.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "grama.urls"
WSGI_APPLICATION = "grama.wsgi.application"
ASGI_APPLICATION = "grama.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Users ------------------------------------------------------------------

# Officials log in with a username and a password. Citizens have no account.
# docs/authorization.md describes login, lockout, resets and permissions.
AUTH_USER_MODEL = "accounts.User"

# axes first: it refuses locked-out username + IP pairs before any password check.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.OfficialBackend",
]

# Argon2 for new hashes; older PBKDF2 hashes still work and are upgraded at login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# Every password an official or an admin chooses must pass these.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # At least one capital letter, one small letter, one number and one special character.
    {"NAME": "apps.accounts.validators.PasswordCompositionValidator"},
]

# Lockout (django-axes): 5 failed logins for one username from one IP address
# lock that pair for 15 minutes. Tries during the lockout do not extend it; a
# successful login clears the count. Admins with accounts.unlock_user unlock early.
AXES_FAILURE_LIMIT = env.int("LOGIN_MAX_FAILURES", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("LOGIN_LOCKOUT_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_USERNAME_CALLABLE = "apps.accounts.lockout.axes_username"
AXES_CLIENT_IP_CALLABLE = "apps.accounts.lockout.axes_client_ip"
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.lockout_response"
# Successful logins are in the audit log already.
AXES_DISABLE_ACCESS_LOG = True
AXES_VERBOSE = False
# One row per failed login (username, IP, time), read by the IP-wide cap below.
# Housekeeping deletes rows older than the cap's window.
AXES_ENABLE_ACCESS_FAILURE_LOG = True

# IP-wide cap against password spraying: this many failed logins from one IP
# address, for any usernames, within the window block every login from that IP
# until the oldest of them leaves the window. 0 switches the cap off. Mobile
# networks share IPs, so keep it well above what one office mistypes in an hour.
LOGIN_IP_MAX_FAILURES = env.int("LOGIN_IP_MAX_FAILURES", default=50)
LOGIN_IP_WINDOW_MINUTES = env.int("LOGIN_IP_WINDOW_MINUTES", default=60)

# "Change password" with a wrong current password: this many within the window end every session
# of the user (apps/accounts/credentials.py), so guessing needs a real login, where the lockout applies.
PASSWORD_CHANGE_MAX_FAILURES = env.int("PASSWORD_CHANGE_MAX_FAILURES", default=5)
PASSWORD_CHANGE_WINDOW_MINUTES = env.int("PASSWORD_CHANGE_WINDOW_MINUTES", default=15)

# Web console sessions: HttpOnly cookie, CSRF token, ended after this much idle time.
CONSOLE_SESSION_IDLE_MINUTES = env.int("CONSOLE_SESSION_IDLE_MINUTES", default=30)
SESSION_COOKIE_AGE = CONSOLE_SESSION_IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
# The console reads the csrftoken cookie and sends it back as X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)

# --- Language and time ------------------------------------------------------

LANGUAGE_CODE = "te"
LANGUAGES = [
    ("te", "తెలుగు"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True

TIME_ZONE = "Asia/Kolkata"
USE_TZ = True

# --- Static files, media and the ported web prototypes ----------------------

STATIC_URL = "/static/"
MEDIA_URL = "/media/"

def _dir_from_env(name: str, default: str) -> Path:
    """A folder from env; relative paths count from the repo root, not the shell."""
    path = Path(env.str(name, default=default))
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


# The website and the Super Admin console live in one repo, BVTGramaConnect,
# cloned next to this one. Run `npm run build` there first: it writes dist/
# (website, served at /) and dist/console/ (console, served at /console/).
# Django serves them only while SERVE_FILES is on; Nginx serves them on the server.
WEB_PUBLIC_DIR = _dir_from_env("WEB_PUBLIC_DIR", "../BVTGramaConnect/dist")
WEB_ADMIN_DIR = _dir_from_env("WEB_ADMIN_DIR", "../BVTGramaConnect/dist/console")

# Django itself serves /media/, /static/, /console/ and the website (grama/sitefiles.py). Off here
# and in prod.py, where Nginx serves them; on in dev.py, also on a server without Nginx (the qa
# server) that runs with DEBUG=False.
SERVE_FILES = False

# --- REST API ----------------------------------------------------------------

REST_FRAMEWORK = {
    # The app: Bearer JWT. The console: session cookie with CSRF. Both check
    # token_version on every request (apps/accounts/authentication.py).
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.GramaJWTAuthentication",
        "apps.accounts.authentication.GramaSessionAuthentication",
    ],
    "EXCEPTION_HANDLER": "grama.exceptions.exception_handler",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Every time goes out in India Standard Time (+05:30), see grama/renderers.py.
    "DEFAULT_RENDERER_CLASSES": [
        "grama.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "grama.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Telugu goes out as raw UTF-8, not \u escapes: half the bytes on 2G.
    "UNICODE_JSON": True,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    # Only views that name a throttle class are limited; there is no default throttle.
    "DEFAULT_THROTTLE_RATES": {"transliterate": env.str("TRANSLITERATION_RATE", default="60/min")},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

API_DOCS_PUBLIC = False

SPECTACULAR_SETTINGS = {
    "TITLE": "BVT Grama Connect API",
    "DESCRIPTION": "Village news platform for Andhra Pradesh. Contract: docs/api-contract.md.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # /api/schema/ and /api/docs/: console users only unless API_DOCS_PUBLIC (dev.py turns it on).
    "SERVE_PERMISSIONS": ["apps.accounts.permissions.CanReadApiDocs"],
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
}

# --- Background jobs (django-q2 with the Django ORM as broker; no Redis) -----

Q_CLUSTER = {
    "name": "grama",
    "orm": "default",
    "workers": env.int("Q_WORKERS", default=2),
    "timeout": 120,
    "retry": 180,
    "max_attempts": 3,
    "queue_limit": 50,
    "bulk": 10,
    "catch_up": False,
    "label": "Background tasks",
}

# --- Platform rules (CLAUDE.md "Configurable rules"; each can be set in env) --

ADMIN_POSTS_AUTO_PUBLISH = env.bool("ADMIN_POSTS_AUTO_PUBLISH", default=True)
REVIEW_FIRST_DECISION_WINS = env.bool("REVIEW_FIRST_DECISION_WINS", default=True)
REVIEW_ESCALATION_HOURS = env.int("REVIEW_ESCALATION_HOURS", default=24)
SUPER_ADMIN_CAN_POST = env.bool("SUPER_ADMIN_CAN_POST", default=False)

# Limits shown to the apps through /me (docs/api-contract.md).
MAX_PHOTOS_PER_POST = env.int("MAX_PHOTOS_PER_POST", default=5)
# Areas one post may be for (owner decision of 2026-10-28). Unticking one village of a whole
# district already gives 60-100 areas, so not 50.
POST_MAX_TARGETS = env.int("POST_MAX_TARGETS", default=200)
# Owner decision Q8: 60-second video posts are in version 1.
VIDEO_POSTS_ENABLED = env.bool("VIDEO_POSTS_ENABLED", default=True)

# --- Client addresses -----------------------------------------------------------

# Read the caller's IP from X-Real-IP. Only safe behind a proxy that sets it.
# Used by the login lockout and the contact form limit.
TRUST_X_REAL_IP = env.bool("TRUST_X_REAL_IP", default=False)

# --- Nearest village -------------------------------------------------------------

NEAREST_VILLAGE_START_KM = env.float("NEAREST_VILLAGE_START_KM", default=5.0)
NEAREST_VILLAGE_MAX_KM = env.float("NEAREST_VILLAGE_MAX_KM", default=50.0)

# --- Posts and review ------------------------------------------------------------

# Uploaded photos: checked, re-encoded (drops EXIF), stored as WebP.
MAX_IMAGE_UPLOAD_MB = env.int("MAX_IMAGE_UPLOAD_MB", default=10)
POST_IMAGE_MAX_PX = 1280
# A lighter copy of every photo, kept for a possible low-data mode. Not sent by the API today:
# the feed's image_url is the 1280 px file, because 640 px looked soft on big cards.
POST_MEDIUM_PX = env.int("POST_MEDIUM_PX", default=640)
POST_THUMB_PX = 320
POST_IMAGE_QUALITY = 80
# Video posts (owner decision Q8): MP4 or MOV, one per post, never with photos.
# ffprobe and ffmpeg (installed by deploy/setup.sh) measure the length and make
# the poster; without them the app must send duration_s and a poster image.
MAX_VIDEO_UPLOAD_MB = env.int("MAX_VIDEO_UPLOAD_MB", default=50)
VIDEO_MAX_SECONDS = env.int("VIDEO_MAX_SECONDS", default=60)
# How often the escalation job runs (django-q2 schedule, see `ensure_schedules`).
ESCALATION_CHECK_MINUTES = env.int("ESCALATION_CHECK_MINUTES", default=15)
# Failed background jobs older than this are deleted by the daily housekeeping job (grama/housekeeping.py).
HOUSEKEEPING_KEEP_DAYS = env.int("HOUSEKEEPING_KEEP_DAYS", default=30)

# --- Public website (web_public: /api/v1/public/site-config/) -------------------

# Defaults are the approved prototype's contact details. Store URLs stay
# empty until the apps are published; the website then shows "coming soon".
SITE_OFFICE_ADDRESS_TE = env.str(
    "SITE_OFFICE_ADDRESS_TE",
    default="BVT Grama Connect, 2nd Floor, Collectorate Road,\nGuntur – 522004, Andhra Pradesh",
)
SITE_OFFICE_ADDRESS_EN = env.str(
    "SITE_OFFICE_ADDRESS_EN",
    default="BVT Grama Connect, 2nd Floor, Collectorate Road,\nGuntur – 522004, Andhra Pradesh",
)
SITE_PHONE = env.str("SITE_PHONE", default="+91 863 223 4567")
SITE_EMAIL = env.str("SITE_EMAIL", default="help@bvtgramaconnect.in")
SITE_HOURS_TE = env.str("SITE_HOURS_TE", default="సోమ–శని 10:00–18:00")
SITE_HOURS_EN = env.str("SITE_HOURS_EN", default="Mon–Sat 10:00–18:00")
PLAY_STORE_URL = env.str("PLAY_STORE_URL", default="")
APP_STORE_URL = env.str("APP_STORE_URL", default="")
CONTACT_MESSAGES_PER_IP_PER_HOUR = env.int("CONTACT_MESSAGES_PER_IP_PER_HOUR", default=5)
# Link added to shared posts and invites (owner decision Q17). Placeholder until the domain is known.
SITE_WEBSITE_URL = env.str("SITE_WEBSITE_URL", default="https://example.in/")
# Village whose feed fills the home page's phone picture (owner decision Q16).
# Empty: the newest published posts anywhere.
SITE_SHOWCASE_VILLAGE_ID = env.int("SITE_SHOWCASE_VILLAGE_ID", default=0) or None
# --- Geography (apps.geography) -------------------------------------------------
# Public geography lists: browsers and phones may reuse them this long (ETag still gives 304s).
GEO_PUBLIC_MAX_AGE = env.int("GEO_PUBLIC_MAX_AGE", default=600)
# Andhra Pradesh bounding box, for the "outside the state" and "swapped" warnings on villages.
GEO_BOUNDS = {"lat": (12.6, 19.95), "lng": (76.7, 84.8)}

# --- Important numbers (apps.contacts, docs/api-contract.md section 14) --------
# Cap on active numbers in one area, so the citizen response stays small on 2G.
CONTACTS_MAX_PER_AREA = env.int("CONTACTS_MAX_PER_AREA", default=200)
# Seconds of Cache-Control on GET /contacts/; an admin's change reaches citizens within this.
CONTACTS_PUBLIC_MAX_AGE = env.int("CONTACTS_PUBLIC_MAX_AGE", default=300)
# Numbers nobody confirmed for this many days show as "needs re-check" to admins.
CONTACTS_VERIFY_DAYS = env.int("CONTACTS_VERIFY_DAYS", default=180)
# Decision C2: a 10-digit mobile is published only after the admin ticks the consent line.
CONTACTS_REQUIRE_MOBILE_CONSENT = env.bool("CONTACTS_REQUIRE_MOBILE_CONSENT", default=True)
CONTACTS_IMPORT_MAX_BYTES = env.int("CONTACTS_IMPORT_MAX_BYTES", default=2 * 1024 * 1024)
CONTACTS_IMPORT_MAX_ROWS = env.int("CONTACTS_IMPORT_MAX_ROWS", default=5000)

# --- Citizen posts (apps.citizens, docs/api-contract.md section 15) -----------
# Citizens never sign in: each phone gets a posting token after Google Play Integrity
# confirms our genuine app. Every citizen post goes to review.
CITIZEN_POSTS_ENABLED = env.bool("CITIZEN_POSTS_ENABLED", default=True)
# Off skips Google's check (debug builds, the qa server); prod.py turns it on and it stays on.
CITIZEN_INTEGRITY_REQUIRED = env.bool("CITIZEN_INTEGRITY_REQUIRED", default=False)
PLAY_INTEGRITY_PACKAGE = env.str("PLAY_INTEGRITY_PACKAGE", default="")
PLAY_INTEGRITY_CREDENTIALS_FILE = env.str("PLAY_INTEGRITY_CREDENTIALS_FILE", default="")
CITIZEN_INTEGRITY_VERIFIER = env.str(
    "CITIZEN_INTEGRITY_VERIFIER", default="apps.citizens.integrity.PlayIntegrityVerifier"
)
# How long a challenge nonce, and Google's verdict on it, stays valid.
CITIZEN_CHALLENGE_SECONDS = env.int("CITIZEN_CHALLENGE_SECONDS", default=300)
# Posts per phone in 24 hours: 5 by default (lead's decision of 2026-10-27). The Super Admin
# changes it in the console (Settings, stored as a SettingOverride); 0 is no limit.
CITIZEN_POSTS_PER_DAY = env.int("CITIZEN_POSTS_PER_DAY", default=5)
# Areas a citizen's post may be for: 0 is the same as POST_MAX_TARGETS. And whether a citizen may
# post to Everyone, which puts the post in the Super Admin's queue. The owner's decision of
# 2026-10-28 lets citizens choose like officials; these narrow it without a code change.
CITIZEN_MAX_TARGETS = env.int("CITIZEN_MAX_TARGETS", default=0)
CITIZEN_CAN_TARGET_STATE = env.bool("CITIZEN_CAN_TARGET_STATE", default=True)
# Photos per post and new phones per IP address in 24 hours: 0 (the default) is no limit.
CITIZEN_MAX_PHOTOS = env.int("CITIZEN_MAX_PHOTOS", default=0)
CITIZEN_REGISTRATIONS_PER_IP_PER_DAY = env.int("CITIZEN_REGISTRATIONS_PER_IP_PER_DAY", default=0)

# --- Service requests (apps.service_requests, docs/api-contract.md section 16) ---
# Requests one citizen phone (all its installs) may send in 24 hours; 0 is no limit.
SERVICE_REQUESTS_PER_DAY = env.int("SERVICE_REQUESTS_PER_DAY", default=10)
# Seconds phones may reuse the public services list.
SERVICES_PUBLIC_MAX_AGE = env.int("SERVICES_PUBLIC_MAX_AGE", default=300)
# Service logos: JPEG, PNG or WebP up to this size, at least the minimum on each side,
# stored as WebP with the longest side at most SERVICE_LOGO_PX.
SERVICE_LOGO_MAX_UPLOAD_MB = env.int("SERVICE_LOGO_MAX_UPLOAD_MB", default=2)
SERVICE_LOGO_MIN_PX = env.int("SERVICE_LOGO_MIN_PX", default=64)
SERVICE_LOGO_PX = env.int("SERVICE_LOGO_PX", default=256)

# --- Home banner ads (apps.ads, docs/api-contract.md section 17) -------------
ADS_MAX_UPLOAD_MB = env.int("ADS_MAX_UPLOAD_MB", default=5)
ADS_MIN_WIDTH_PX = env.int("ADS_MIN_WIDTH_PX", default=800)
# Stored as WebP with the longest side at most this.
ADS_IMAGE_MAX_PX = env.int("ADS_IMAGE_MAX_PX", default=1600)
# Video banners: the same numbers as post videos today, kept apart so banners can be
# tightened alone. The app has them too (kAdVideoMaxSec, kAdVideoMaxMb): tell the mobile team.
ADS_VIDEO_MAX_MB = env.int("ADS_VIDEO_MAX_MB", default=50)
ADS_VIDEO_MAX_SECONDS = env.int("ADS_VIDEO_MAX_SECONDS", default=60)
# Ads one village's Home shows, and how long phones may reuse the list.
ADS_PUBLIC_LIMIT = env.int("ADS_PUBLIC_LIMIT", default=10)
ADS_PUBLIC_MAX_AGE = env.int("ADS_PUBLIC_MAX_AGE", default=300)

# Help-line numbers for the app (owner decision Q19), labels from the approved prototype.
# Statewide contacts in apps.contacts replace them once loaded; this list is the fallback.
# Override with SITE_HELPLINES as a JSON list of {"number", "label_te", "label_en"}.
SITE_HELPLINES = env.json(
    "SITE_HELPLINES",
    default=[
        {"number": "112", "label_te": "అత్యవసరం", "label_en": "Emergency"},
        {"number": "108", "label_te": "అంబులెన్స్", "label_en": "Ambulance"},
        {"number": "1930", "label_te": "సైబర్ మోసం", "label_en": "Cyber fraud"},
        {"number": "181", "label_te": "మహిళా హెల్ప్‌లైన్", "label_en": "Women helpline"},
        {"number": "1098", "label_te": "బాలల హెల్ప్‌లైన్", "label_en": "Child helpline"},
        {"number": "1912", "label_te": "విద్యుత్", "label_en": "Electricity"},
    ],
)

# --- Feed, push and notifications ----------------------------------------------

# Citizen feed: posts per cursor page.
FEED_PAGE_SIZE = 20
# "Don't miss" (events from today on) and the urgent banner (owner decisions Q7, Q19).
FEED_UPCOMING_LIMIT = 10
FEED_URGENT_LIMIT = 5
FEED_URGENT_DAYS = 7
# News search needs at least this many characters.
FEED_SEARCH_MIN_CHARS = 2

# Dotted path to a notifications.push.PushGateway subclass. FcmGateway sends
# nothing (and logs once) until FCM_CREDENTIALS_FILE points at a Firebase
# service-account JSON file.
PUSH_GATEWAY = env.str("PUSH_GATEWAY", default="apps.notifications.push.FcmGateway")
FCM_CREDENTIALS_FILE = env.str("FCM_CREDENTIALS_FILE", default="")
FCM_PROJECT_ID = env.str("FCM_PROJECT_ID", default="")

# Machine translation of published posts, Telugu <-> English (apps.posts.translation; contract
# section 6): "off", "google" or "bhashini". Off: nothing is sent anywhere and `translation` is null.
TRANSLATION_PROVIDER = env.str("TRANSLATION_PROVIDER", default="off")
GOOGLE_TRANSLATE_KEY = env.str("GOOGLE_TRANSLATE_KEY", default="")
BHASHINI_USER_ID = env.str("BHASHINI_USER_ID", default="")
BHASHINI_API_KEY = env.str("BHASHINI_API_KEY", default="")
BHASHINI_PIPELINE_ID = env.str("BHASHINI_PIPELINE_ID", default="")
TRANSLATION_TIMEOUT_S = env.int("TRANSLATION_TIMEOUT_S", default=20)
# Waits before trying a failed translation again; after the last, the post stays untranslated.
TRANSLATION_RETRY_MINUTES = [1, 5, 15]

# Telugu spelling of names typed in English, suggested in the admins' Telugu name boxes
# (apps.web_admin.transliteration; contract section 19): "google_input_tools" or "off".
TRANSLITERATION_PROVIDER = env.str("TRANSLITERATION_PROVIDER", default="google_input_tools")
TRANSLITERATION_TIMEOUT_S = env.float("TRANSLITERATION_TIMEOUT_S", default=4.0)
TRANSLITERATION_CACHE_S = env.int("TRANSLITERATION_CACHE_S", default=60 * 60 * 24 * 30)  # 30 days

# Email and push retries: tries and the first wait in seconds (then x4 each time).
DELIVERY_RETRY_ATTEMPTS = env.int("DELIVERY_RETRY_ATTEMPTS", default=3)
DELIVERY_RETRY_BASE_SECONDS = env.float("DELIVERY_RETRY_BASE_SECONDS", default=1.0)

# --- Logging -----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        # Lockouts are in the audit log; keep axes to warnings (it never logs passwords).
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

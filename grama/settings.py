"""Unified Django Settings for GramaConnect.
 
Only two environments: DEV and PROD, controlled entirely by standard DEBUG:
- DEV (default): DEBUG=True in .env (or unset). Uses SQLite, local file serving.
- PROD: DEBUG=False in .env. Enforces strict security, MySQL/Postgres, HSTS, Nginx serving.
"""
 
from __future__ import annotations
 
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
 
import environ
from django.core.exceptions import ImproperlyConfigured
 
# Build paths inside the project: BASE_DIR / 'subdir'.
# BASE_DIR is the repository root (two levels up from grama/settings.py).
BASE_DIR = Path(__file__).resolve().parent.parent
 
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")
 
# In production, set DEBUG=False in .env; defaults to True for local development.
DEBUG = env.bool("DEBUG", default=True)
 
# Tests: pytest is imported AND has set PYTEST_VERSION (pytest 8.2+ sets it for the run), or
# `manage.py test`. Both pytest signals are needed, so a server process that merely imports pytest
# never switches to the test settings (MD5 hashing, no push).
_RUNNING_TESTS = ("pytest" in sys.modules and "PYTEST_VERSION" in os.environ) or (
    len(sys.argv) > 1 and sys.argv[1] == "test"
)
 
 
# ==============================================================================
# 1. CORE SECURITY & HOSTS (DEV vs PROD)
# ==============================================================================
 
SECRET_KEY = env.str(
    "SECRET_KEY",
    default="django-insecure-dev-only-0f3a9c1e7b5d4f2a8e6c9b1d3f5a7c9e" if DEBUG else None,
)
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]", "10.0.2.2"] if DEBUG else [],
)
 
if not DEBUG and (
    SECRET_KEY.startswith("django-insecure-")
    or len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or "change-me" in SECRET_KEY
):
    raise ImproperlyConfigured(
        "SECRET_KEY is an example value or too weak. Generate one with: "
        "python3 -c 'import secrets; print(secrets.token_urlsafe(50))'"
    )
 
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# DEBUG is on unless .env says otherwise, which is right for laptops and wrong for a server whose
# .env lost its DEBUG=False line. A server is recognised by a domain name in ALLOWED_HOSTS:
# development mode refuses to start with one. Laptops use localhost and IP addresses.
_LOCAL_HOST = re.compile(r"^(localhost|\.?[\w-]+\.(localhost|local)|\[[0-9a-fA-F:]+\]|[0-9.]+)$")
if DEBUG and not _RUNNING_TESTS and not env.bool("DEBUG_ON_PUBLIC_HOST", default=False):
    _public_hosts = [host for host in ALLOWED_HOSTS if not _LOCAL_HOST.match(host)]
    if _public_hosts:
        raise ImproperlyConfigured(
            f"DEBUG is on (it is unset or True) but ALLOWED_HOSTS names {', '.join(_public_hosts)}. "
            "A server must set DEBUG=False in its .env. On a laptop, remove those hosts, or set "
            "DEBUG_ON_PUBLIC_HOST=True if you really mean to run development mode under that name."
        )
 
 
# ==============================================================================
# 2. DATABASE CONFIGURATION (DEV vs PROD)
# ==============================================================================
 
def database_config(url: str) -> dict:
    """Turn a DATABASE_URL into Django's DATABASES['default']."""
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
 
 
SQLITE_DATABASE = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": env.str("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
    "OPTIONS": {
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL",
    },
}
 
if _RUNNING_TESTS:
    # Tests always run on SQLite, whatever DEBUG and DATABASE_URL say: never on a server's database.
    DATABASES = {"default": SQLITE_DATABASE}
elif not DEBUG:
    DATABASES = {"default": database_config(env.str("DATABASE_URL"))}
else:  # DEV
    _db_url = env.str("DATABASE_URL", default="")
    DATABASES = {"default": database_config(_db_url) if _db_url else SQLITE_DATABASE}
 
 
# ==============================================================================
# 3. STATIC FILES & MEDIA UPLOADS (DEV vs PROD)
# ==============================================================================
 
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
 
def _dir_from_env(name: str, default: str) -> Path:
    path = Path(env.str(name, default=default))
    return path if path.is_absolute() else (BASE_DIR / path).resolve()
 
WEB_PUBLIC_DIR = _dir_from_env("WEB_PUBLIC_DIR", "../BVTGramaConnect/dist")
WEB_ADMIN_DIR = _dir_from_env("WEB_ADMIN_DIR", "../BVTGramaConnect/dist/console")
 
if _RUNNING_TESTS:
    # Never a server's media folder, whatever DEBUG says.
    MEDIA_ROOT = BASE_DIR / ".test-media"
    STATIC_ROOT = BASE_DIR / "staticfiles"
    SERVE_FILES = env.bool("SERVE_FILES", default=DEBUG)
elif not DEBUG:
    MEDIA_ROOT = Path(env.str("MEDIA_ROOT"))
    STATIC_ROOT = Path(env.str("STATIC_ROOT"))
    SERVE_FILES = env.bool("SERVE_FILES", default=False)  # Nginx serves files in production
else:  # DEV
    MEDIA_ROOT = BASE_DIR / "media"
    STATIC_ROOT = BASE_DIR / "staticfiles"
    SERVE_FILES = env.bool("SERVE_FILES", default=True)   # Django serves files in dev
 
 
# ==============================================================================
# 4. HTTPS, COOKIES & REVERSE PROXY (DEV vs PROD)
# ==============================================================================
 
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    TRUST_X_REAL_IP = env.bool("TRUST_X_REAL_IP", default=True)
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
    SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
    SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W005", "security.W021"]
    SECURE_REFERRER_POLICY = "same-origin"
    CITIZEN_INTEGRITY_REQUIRED = env.bool("CITIZEN_INTEGRITY_REQUIRED", default=True)
    API_DOCS_PUBLIC = env.bool("API_DOCS_PUBLIC", default=False)
else:  # DEV
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
    if env.bool("TRUST_PROXY_HTTPS", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    TRUST_X_REAL_IP = env.bool("TRUST_X_REAL_IP", default=False)
    CITIZEN_INTEGRITY_REQUIRED = env.bool("CITIZEN_INTEGRITY_REQUIRED", default=False)
    API_DOCS_PUBLIC = env.bool("API_DOCS_PUBLIC", default=True)
    SILENCED_SYSTEM_CHECKS = []
    SECURE_HSTS_SECONDS = 0

# /django-admin/ (grama/urls.py): on for laptops (DEBUG on), off on every server, qa included (lead's
# decision of 2026-11-11). Off, the address does not exist. Server work goes through the console or
# `manage.py shell`. Turn it on for a server only by writing DJANGO_ADMIN_ENABLED=True in its .env.
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=DEBUG)
 
 
# ==============================================================================
# 5. CACHE & THROTTLING (DEV vs PROD)
# ==============================================================================
 
if not DEBUG:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "throttle": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "grama_throttle_cache"},
    }
else:  # DEV
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "throttle": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "throttle"},
    }
 
LOGIN_THROTTLE_RATE = env.str("LOGIN_THROTTLE_RATE", default="30/hour")
 
 
# ==============================================================================
# 6. EMAIL, PUSH NOTIFICATIONS & TEST ACCELERATION
# ==============================================================================
 
if _RUNNING_TESTS:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    PUSH_GATEWAY = "apps.notifications.push.LocmemPushGateway"
    TRANSLITERATION_PROVIDER = "off"
    DELIVERY_RETRY_BASE_SECONDS = 0
else:
    PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
        "django.contrib.auth.hashers.ScryptPasswordHasher",
    ]
    EMAIL_BACKEND = env.str("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
    PUSH_GATEWAY = env.str("PUSH_GATEWAY", default="apps.notifications.push.FcmGateway")
    TRANSLITERATION_PROVIDER = env.str("TRANSLITERATION_PROVIDER", default="google_input_tools")
    DELIVERY_RETRY_BASE_SECONDS = env.float("DELIVERY_RETRY_BASE_SECONDS", default=1.0)
 
 
# ==============================================================================
# 7. APPLICATIONS & MIDDLEWARE (COMMON)
# ==============================================================================
 
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
 
THIRD_PARTY_APPS = [
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
    "apps.share",
]
 
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS
 
MIDDLEWARE = [
    # First: every answer, even an early error, carries X-Request-ID (grama/request_id.py).
    "grama.request_id.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.http.ConditionalGetMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
 
 
# ==============================================================================
# 8. USERS & AUTHENTICATION (COMMON)
# ==============================================================================
 
AUTH_USER_MODEL = "accounts.User"
 
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.OfficialBackend",
]
 
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.accounts.validators.PasswordCompositionValidator"},
]
 
# Lockout (django-axes)
AXES_FAILURE_LIMIT = env.int("LOGIN_MAX_FAILURES", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("LOGIN_LOCKOUT_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_USERNAME_CALLABLE = "apps.accounts.lockout.axes_username"
AXES_CLIENT_IP_CALLABLE = "apps.accounts.lockout.axes_client_ip"
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.lockout_response"
AXES_DISABLE_ACCESS_LOG = True
AXES_VERBOSE = False
AXES_ENABLE_ACCESS_FAILURE_LOG = True
 
LOGIN_IP_MAX_FAILURES = env.int("LOGIN_IP_MAX_FAILURES", default=50)
LOGIN_IP_WINDOW_MINUTES = env.int("LOGIN_IP_WINDOW_MINUTES", default=60)
PASSWORD_CHANGE_MAX_FAILURES = env.int("PASSWORD_CHANGE_MAX_FAILURES", default=5)
PASSWORD_CHANGE_WINDOW_MINUTES = env.int("PASSWORD_CHANGE_WINDOW_MINUTES", default=15)
 
CONSOLE_SESSION_IDLE_MINUTES = env.int("CONSOLE_SESSION_IDLE_MINUTES", default=30)
SESSION_COOKIE_AGE = CONSOLE_SESSION_IDLE_MINUTES * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
 
 
# ==============================================================================
# 9. LANGUAGE & TIME (COMMON)
# ==============================================================================
 
LANGUAGE_CODE = "te"
LANGUAGES = [
    ("te", "తెలుగు"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True
 
 
# ==============================================================================
# 10. REST FRAMEWORK & OPENAPI (COMMON)
# ==============================================================================
 
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.GramaJWTAuthentication",
        "apps.accounts.authentication.GramaSessionAuthentication",
    ],
    "EXCEPTION_HANDLER": "grama.exceptions.exception_handler",
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
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
    "UNICODE_JSON": True,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "DEFAULT_THROTTLE_RATES": {"transliterate": env.str("TRANSLITERATION_RATE", default="60/min")},
}
 
if DEBUG and not _RUNNING_TESTS:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append("rest_framework.renderers.BrowsableAPIRenderer")
 
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
 
SPECTACULAR_SETTINGS = {
    "TITLE": "BVT Grama Connect API",
    "DESCRIPTION": "Village news platform for Andhra Pradesh. Contract: docs/api-contract.md.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["apps.accounts.permissions.CanReadApiDocs"],
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    # One stable name per choice set used in several places, instead of generated names such as
    # "Level360Enum" that change when a serializer changes and break generated client code.
    "ENUM_NAME_OVERRIDES": {
        "AreaLevelEnum": "apps.geography.models.AreaLevel",
        "LanguageEnum": "apps.accounts.models.Language",
        "PostStatusEnum": "apps.posts.models.PostStatus",
        "ServiceRequestStatusEnum": "apps.service_requests.models.RequestStatus",
        "DevicePlatformEnum": "apps.notifications.models.Platform",
        "MediaKindEnum": "apps.posts.models.MediaKind",
        "NotificationKindEnum": "apps.notifications.models.NotificationKind",
        "ServiceIconEnum": "apps.service_requests.models.ICONS",
        "BillIconEnum": "apps.bills.models.ICONS",
    },
}
 
 
# ==============================================================================
# 11. BACKGROUND WORKER (DJANGO-Q2)
# ==============================================================================
 
Q_CLUSTER = {
    "name": "grama",
    "orm": "default",
    "workers": env.int("Q_WORKERS", default=2),
    # Seconds between queue checks while idle. django-q2's default, 0.2, asks the database five times a
    # second all day; 1 keeps pushes prompt at a fifth of the load (audit finding BE-DB-017).
    "poll": env.float("Q_POLL", default=1.0),
    "timeout": 120,
    "retry": 180,
    "max_attempts": 3,
    "queue_limit": 50,
    "bulk": 10,
    "catch_up": False,
    "label": "Background tasks",
    "sync": _RUNNING_TESTS,
}
 
 
# ==============================================================================
# 12. PLATFORM RULES & APP SETTINGS (COMMON)
# ==============================================================================
 
ADMIN_POSTS_AUTO_PUBLISH = env.bool("ADMIN_POSTS_AUTO_PUBLISH", default=True)
REVIEW_FIRST_DECISION_WINS = env.bool("REVIEW_FIRST_DECISION_WINS", default=True)
REVIEW_ESCALATION_HOURS = env.int("REVIEW_ESCALATION_HOURS", default=24)
# Overdue posts escalated per run of the escalation job (every ESCALATION_CHECK_MINUTES), oldest first;
# the rest wait for the next run, so one run stays well inside the job timeout.
ESCALATION_BATCH_SIZE = env.int("ESCALATION_BATCH_SIZE", default=200)
if ESCALATION_BATCH_SIZE < 1:
    raise ImproperlyConfigured("ESCALATION_BATCH_SIZE must be at least 1.")
SUPER_ADMIN_CAN_POST = env.bool("SUPER_ADMIN_CAN_POST", default=True)
MAX_SUPER_ADMINS = env.int("MAX_SUPER_ADMINS", default=5)
if MAX_SUPER_ADMINS < 0:
    raise ImproperlyConfigured("MAX_SUPER_ADMINS must be 0 (no limit) or more.")
 
# Photos per official's or Super Admin's post: 0 (the default) is no limit, as for citizens
# (lead's decision of 2026-11-11). Videos have no count limit; photos and videos may be mixed.
MAX_PHOTOS_PER_POST = env.int("MAX_PHOTOS_PER_POST", default=0)
if MAX_PHOTOS_PER_POST < 0:
    raise ImproperlyConfigured("MAX_PHOTOS_PER_POST must be 0 (no limit) or more.")
POST_MAX_TARGETS = env.int("POST_MAX_TARGETS", default=200)
VIDEO_POSTS_ENABLED = env.bool("VIDEO_POSTS_ENABLED", default=True)
 
NEAREST_VILLAGE_START_KM = env.float("NEAREST_VILLAGE_START_KM", default=5.0)
NEAREST_VILLAGE_MAX_KM = env.float("NEAREST_VILLAGE_MAX_KM", default=50.0)
 
MAX_IMAGE_UPLOAD_MB = env.int("MAX_IMAGE_UPLOAD_MB", default=10)
POST_IMAGE_MAX_PX = 1280
POST_MEDIUM_PX = env.int("POST_MEDIUM_PX", default=640)
POST_THUMB_PX = 320
POST_IMAGE_QUALITY = 80
 
MAX_VIDEO_UPLOAD_MB = env.int("MAX_VIDEO_UPLOAD_MB", default=50)
VIDEO_MAX_SECONDS = env.int("VIDEO_MAX_SECONDS", default=60)
# How long the server may spend checking uploads (apps.posts.media, audit BE-PERF-003): each
# ffprobe/ffmpeg step, and all photos and videos of one request together. A step that runs out of
# time answers 415; a request over the budget answers 400. Keep the budget below Gunicorn's
# --timeout (GUNICORN_TIMEOUT, 120 s), so a request is refused cleanly instead of being killed.
VIDEO_PROBE_TIMEOUT_S = env.int("VIDEO_PROBE_TIMEOUT_S", default=15)
VIDEO_POSTER_TIMEOUT_S = env.int("VIDEO_POSTER_TIMEOUT_S", default=20)
VIDEO_STRIP_TIMEOUT_S = env.int("VIDEO_STRIP_TIMEOUT_S", default=45)
MEDIA_PROCESSING_BUDGET_S = env.int("MEDIA_PROCESSING_BUDGET_S", default=90)
if min(VIDEO_PROBE_TIMEOUT_S, VIDEO_POSTER_TIMEOUT_S, VIDEO_STRIP_TIMEOUT_S, MEDIA_PROCESSING_BUDGET_S) < 1:
    raise ImproperlyConfigured("VIDEO_*_TIMEOUT_S and MEDIA_PROCESSING_BUDGET_S must be at least 1 second.")
ESCALATION_CHECK_MINUTES = env.int("ESCALATION_CHECK_MINUTES", default=15)
HOUSEKEEPING_KEEP_DAYS = env.int("HOUSEKEEPING_KEEP_DAYS", default=30)
NOTIFICATION_KEEP_DAYS = env.int("NOTIFICATION_KEEP_DAYS", default=90)  # read ones; 0 keeps them all
# Longest text kept in an audit entry's data; longer text is cut with "…" (apps.audit.services).
AUDIT_TEXT_MAX_CHARS = env.int("AUDIT_TEXT_MAX_CHARS", default=2000)
if AUDIT_TEXT_MAX_CHARS < 10:
    raise ImproperlyConfigured("AUDIT_TEXT_MAX_CHARS must be at least 10.")
 
PLAY_STORE_URL = env.str("PLAY_STORE_URL", default="")
APP_STORE_URL = env.str("APP_STORE_URL", default="")
CONTACT_MESSAGES_PER_IP_PER_HOUR = env.int("CONTACT_MESSAGES_PER_IP_PER_HOUR", default=5)
# The console lists contact messages from this many days back; older ones are kept, not shown.
CONTACT_MESSAGES_VISIBLE_DAYS = env.int("CONTACT_MESSAGES_VISIBLE_DAYS", default=90)
SITE_WEBSITE_URL = env.str("SITE_WEBSITE_URL", default="https://example.in/")
 
PUBLIC_BASE_URL = env.str("PUBLIC_BASE_URL", default="")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.endswith("/"):
    PUBLIC_BASE_URL += "/"
if PUBLIC_BASE_URL and not re.match(r"^https?://[^/]+/", PUBLIC_BASE_URL):
    raise ImproperlyConfigured("PUBLIC_BASE_URL must be an address like https://www.gramaconnect.com/.")
 
ANDROID_PACKAGE = env.str("ANDROID_PACKAGE", default="com.bvt.app")
ANDROID_CERT_SHA256 = [
    value.strip().upper()
    for value in env.list(
        "ANDROID_CERT_SHA256",
        default=["27:16:13:C6:5C:CD:BE:3A:45:97:E0:54:4E:CD:09:CA:44:91:E0:CB:07:63:B3:5E:6E:D0:42:89:D5:8A:89:79"],
    )
    if value.strip()
]
for _fingerprint in ANDROID_CERT_SHA256:
    if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){31}", _fingerprint):
        raise ImproperlyConfigured(f"ANDROID_CERT_SHA256: {_fingerprint!r} is not a SHA-256 fingerprint (32 hex pairs).")
 
SHARE_PAGE_MAX_AGE = env.int("SHARE_PAGE_MAX_AGE", default=300)
APP_DOWNLOAD_URL = env.str("APP_DOWNLOAD_URL", default="")
SITE_SHOWCASE_VILLAGE_ID = env.int("SITE_SHOWCASE_VILLAGE_ID", default=0) or None
 
GEO_PUBLIC_MAX_AGE = env.int("GEO_PUBLIC_MAX_AGE", default=600)
GEO_BOUNDS = {"lat": (12.6, 19.95), "lng": (76.7, 84.8)}
# geo/villages/import/ reads the whole file into memory: the state's ~18,000 villages are about 3 MB.
GEO_IMPORT_MAX_BYTES = env.int("GEO_IMPORT_MAX_BYTES", default=20 * 1024 * 1024)
 
CONTACTS_MAX_PER_AREA = env.int("CONTACTS_MAX_PER_AREA", default=200)
CONTACTS_PUBLIC_MAX_AGE = env.int("CONTACTS_PUBLIC_MAX_AGE", default=300)
CONTACTS_VERIFY_DAYS = env.int("CONTACTS_VERIFY_DAYS", default=180)
CONTACTS_REQUIRE_MOBILE_CONSENT = env.bool("CONTACTS_REQUIRE_MOBILE_CONSENT", default=True)
CONTACTS_IMPORT_MAX_BYTES = env.int("CONTACTS_IMPORT_MAX_BYTES", default=2 * 1024 * 1024)
CONTACTS_IMPORT_MAX_ROWS = env.int("CONTACTS_IMPORT_MAX_ROWS", default=5000)
 
CITIZEN_POSTS_ENABLED = env.bool("CITIZEN_POSTS_ENABLED", default=True)
PLAY_INTEGRITY_PACKAGE = env.str("PLAY_INTEGRITY_PACKAGE", default="")
PLAY_INTEGRITY_CREDENTIALS_FILE = env.str("PLAY_INTEGRITY_CREDENTIALS_FILE", default="")
CITIZEN_INTEGRITY_VERIFIER = env.str(
    "CITIZEN_INTEGRITY_VERIFIER", default="apps.citizens.integrity.PlayIntegrityVerifier"
)
CITIZEN_CHALLENGE_SECONDS = env.int("CITIZEN_CHALLENGE_SECONDS", default=300)
CITIZEN_POSTS_PER_DAY = env.int("CITIZEN_POSTS_PER_DAY", default=5)
CITIZEN_MAX_TARGETS = env.int("CITIZEN_MAX_TARGETS", default=0)
CITIZEN_CAN_TARGET_STATE = env.bool("CITIZEN_CAN_TARGET_STATE", default=True)
CITIZEN_MAX_PHOTOS = env.int("CITIZEN_MAX_PHOTOS", default=0)
CITIZEN_REGISTRATIONS_PER_IP_PER_DAY = env.int("CITIZEN_REGISTRATIONS_PER_IP_PER_DAY", default=0)
 
SERVICE_REQUESTS_PER_DAY = env.int("SERVICE_REQUESTS_PER_DAY", default=10)
# Officials' lists (decision of 2026-11-11): a completed or rejected request is listed while its last
# change is at most this many days old; open requests always. Nothing is deleted.
SERVICE_REQUEST_CLOSED_VISIBLE_DAYS = env.int("SERVICE_REQUEST_CLOSED_VISIBLE_DAYS", default=180)
if SERVICE_REQUEST_CLOSED_VISIBLE_DAYS < 1:
    raise ImproperlyConfigured("SERVICE_REQUEST_CLOSED_VISIBLE_DAYS must be at least 1.")
SERVICES_PUBLIC_MAX_AGE = env.int("SERVICES_PUBLIC_MAX_AGE", default=300)
SERVICE_LOGO_MAX_UPLOAD_MB = env.int("SERVICE_LOGO_MAX_UPLOAD_MB", default=2)
SERVICE_LOGO_MIN_PX = env.int("SERVICE_LOGO_MIN_PX", default=64)
SERVICE_LOGO_PX = env.int("SERVICE_LOGO_PX", default=256)
 
ADS_MAX_UPLOAD_MB = env.int("ADS_MAX_UPLOAD_MB", default=5)
ADS_MIN_WIDTH_PX = env.int("ADS_MIN_WIDTH_PX", default=800)
ADS_IMAGE_MAX_PX = env.int("ADS_IMAGE_MAX_PX", default=1600)
ADS_VIDEO_MAX_MB = env.int("ADS_VIDEO_MAX_MB", default=60)
ADS_VIDEO_MAX_SECONDS = env.int("ADS_VIDEO_MAX_SECONDS", default=0)
ADS_PUBLIC_LIMIT = env.int("ADS_PUBLIC_LIMIT", default=10)
ADS_PUBLIC_MAX_AGE = env.int("ADS_PUBLIC_MAX_AGE", default=300)
 
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
 
FEED_PAGE_SIZE = 20
FEED_UPCOMING_LIMIT = 10
FEED_URGENT_LIMIT = 5
FEED_URGENT_DAYS = 7
FEED_SEARCH_MIN_CHARS = 2
 
FCM_CREDENTIALS_FILE = env.str("FCM_CREDENTIALS_FILE", default="")
FCM_PROJECT_ID = env.str("FCM_PROJECT_ID", default="")
FCM_TOPIC_PREFIX = env.str("FCM_TOPIC_PREFIX", default="")
if not re.fullmatch(r"[A-Za-z0-9_.~%-]*", FCM_TOPIC_PREFIX):
    raise ImproperlyConfigured("FCM_TOPIC_PREFIX may hold only letters, digits and - _ . ~ %.")
PUSH_TTL_SECONDS = env.int("PUSH_TTL_SECONDS", default=86400)
PUSH_ALL_POSTS_HIGH_PRIORITY = env.bool("PUSH_ALL_POSTS_HIGH_PRIORITY", default=True)
# A post's topic job stops starting new topics after this many seconds and queues the rest as a new
# job, so it never reaches the worker's time limit (Q_CLUSTER "timeout") and is never re-run.
PUSH_TOPICS_JOB_SECONDS = env.int("PUSH_TOPICS_JOB_SECONDS", default=60)
if not 0 < PUSH_TOPICS_JOB_SECONDS < Q_CLUSTER["timeout"]:
    raise ImproperlyConfigured("PUSH_TOPICS_JOB_SECONDS must be above 0 and below the job timeout (120).")
 
TRANSLATION_PROVIDER = env.str("TRANSLATION_PROVIDER", default="off")
GOOGLE_TRANSLATE_KEY = env.str("GOOGLE_TRANSLATE_KEY", default="")
BHASHINI_USER_ID = env.str("BHASHINI_USER_ID", default="")
BHASHINI_API_KEY = env.str("BHASHINI_API_KEY", default="")
BHASHINI_PIPELINE_ID = env.str("BHASHINI_PIPELINE_ID", default="")
TRANSLATION_TIMEOUT_S = env.int("TRANSLATION_TIMEOUT_S", default=20)
TRANSLATION_RETRY_MINUTES = [1, 5, 15]
 
TRANSLITERATION_TIMEOUT_S = env.float("TRANSLITERATION_TIMEOUT_S", default=4.0)
TRANSLITERATION_CACHE_S = env.int("TRANSLITERATION_CACHE_S", default=60 * 60 * 24 * 30)
DELIVERY_RETRY_ATTEMPTS = env.int("DELIVERY_RETRY_ATTEMPTS", default=3)

# Audit log screens (owner decision of 2026-11-11): the last AUDIT_DEFAULT_DAYS by default, never
# further back than AUDIT_VISIBLE_DAYS; older entries stay in the database. Exports stop at
# AUDIT_EXPORT_MAX_ROWS with a 400 asking for a shorter date range (apps.audit.views).
AUDIT_DEFAULT_DAYS = env.int("AUDIT_DEFAULT_DAYS", default=90)
AUDIT_VISIBLE_DAYS = env.int("AUDIT_VISIBLE_DAYS", default=180)
AUDIT_EXPORT_MAX_ROWS = env.int("AUDIT_EXPORT_MAX_ROWS", default=50000)
if not 1 <= AUDIT_DEFAULT_DAYS <= AUDIT_VISIBLE_DAYS:
    raise ImproperlyConfigured("AUDIT_DEFAULT_DAYS must be at least 1 and no more than AUDIT_VISIBLE_DAYS.")
if AUDIT_EXPORT_MAX_ROWS < 1:
    raise ImproperlyConfigured("AUDIT_EXPORT_MAX_ROWS must be at least 1.")
 
 
# ==============================================================================
# 13. LOGGING (COMMON)
# ==============================================================================
 
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # Every line carries the request's number ("-" in background jobs), the same one the answer
    # sends in X-Request-ID: search the log for it (grama/request_id.py).
    "filters": {
        "request_id": {"()": "grama.request_id.RequestIdFilter"},
    },
    "formatters": {
        "simple": {"format": "{asctime} {levelname} [{request_id}] {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple", "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
 
# Backward compatibility shims for legacy submodule imports
sys.modules.setdefault("grama.settings.base", sys.modules[__name__])
sys.modules.setdefault("grama.settings.dev", sys.modules[__name__])
sys.modules.setdefault("grama.settings.prod", sys.modules[__name__])
sys.modules.setdefault("grama.settings.test", sys.modules[__name__])

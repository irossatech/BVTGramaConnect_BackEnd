"""Local development: SQLite, DEBUG on, no extra services.

Also the qa server, which runs runserver without Nginx: there .env sets DEBUG=False, so unknown
addresses and crashes never show Django's debug pages, while SERVE_FILES keeps Django serving
photos, the console and the website.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, REST_FRAMEWORK, database_config, env

DEBUG = env.bool("DEBUG", default=True)
SERVE_FILES = env.bool("SERVE_FILES", default=True)

SECRET_KEY = env.str(
    "SECRET_KEY",
    default="django-insecure-dev-only-0f3a9c1e7b5d4f2a8e6c9b1d3f5a7c9e",
)

# 10.0.2.2 is how the Android emulator reaches this PC. For a real phone on
# the same Wi-Fi, add the PC's LAN address through ALLOWED_HOSTS in .env.
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]", "10.0.2.2"],
)

SQLITE_DATABASE = {
    "ENGINE": "django.db.backends.sqlite3",
    # SQLITE_PATH lets several checkouts or helpers keep separate dev databases.
    "NAME": env.str("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
    "OPTIONS": {
        # runserver and qcluster share this file. WAL plus IMMEDIATE
        # transactions and a longer wait avoid "database is locked".
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL",
    },
}
# SQLite on laptops. A dev server with its own database (the qa server's MySQL) sets DATABASE_URL,
# the same form prod.py reads; MySQL then gets utf8mb4 and strict mode. Tests always use SQLite.
_database_url = env.str("DATABASE_URL", default="")
DATABASES = {"default": database_config(_database_url) if _database_url else SQLITE_DATABASE}

MEDIA_ROOT = BASE_DIR / "media"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Plain http on localhost: a Secure cookie would not be stored.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)

# A dev server reached through a domain, such as https://dev.bvtgramaconnect.com behind the hosting
# panel's proxy: the console's login form posts from that origin, so it must be trusted, and with
# TRUST_PROXY_HTTPS the proxy's X-Forwarded-Proto tells Django the request came over https.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if env.bool("TRUST_PROXY_HTTPS", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The browsable API pages only while debugging; with DEBUG off every answer is plain JSON.
if DEBUG:
    REST_FRAMEWORK = {
        **REST_FRAMEWORK,
        "DEFAULT_RENDERER_CLASSES": [
            "grama.renderers.JSONRenderer",
            "rest_framework.renderers.BrowsableAPIRenderer",
        ],
    }

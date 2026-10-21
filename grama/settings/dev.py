"""Local development: SQLite, DEBUG on, no extra services."""

from .base import *  # noqa: F403
from .base import BASE_DIR, REST_FRAMEWORK, env

DEBUG = True

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

DATABASES = {
    "default": {
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
}

MEDIA_ROOT = BASE_DIR / "media"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Plain http on localhost: a Secure cookie would not be stored.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

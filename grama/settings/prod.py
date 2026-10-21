"""The Linux server: Gunicorn behind Nginx.

Required env: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT.
Startup fails with ImproperlyConfigured when one is missing.
"""

from pathlib import Path

from .base import *  # noqa: F403
from .base import database_config, env

DEBUG = False

SECRET_KEY = env.str("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# MySQL, PostgreSQL or another engine, from one URL. MySQL gets utf8mb4.
DATABASES = {"default": database_config(env.str("DATABASE_URL"))}

MEDIA_ROOT = Path(env.str("MEDIA_ROOT"))
STATIC_ROOT = Path(env.str("STATIC_ROOT"))

# Nginx terminates HTTPS and passes X-Forwarded-Proto and X-Real-IP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TRUST_X_REAL_IP = env.bool("TRUST_X_REAL_IP", default=True)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

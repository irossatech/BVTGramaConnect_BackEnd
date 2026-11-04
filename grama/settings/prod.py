"""The Linux server: Gunicorn behind Nginx (ours from deploy/setup.sh, or a hosting panel's).

Required env: SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT, STATIC_ROOT.
Startup fails with ImproperlyConfigured when one is missing.
"""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import database_config, env

DEBUG = False

# The key signs every app token and console session: anyone who knows it can make their own
# "logged in as Super Admin" token. setup.sh generates a random one; this stops a hand-written .env
# from starting with the example value, the development key, or anything Django calls weak (W009).
KNOWN_KEYS = {
    "change-me-to-a-long-random-string",  # .env.example
    "django-insecure-dev-only-0f3a9c1e7b5d4f2a8e6c9b1d3f5a7c9e",  # dev.py
}
SECRET_KEY = env.str("SECRET_KEY")
if SECRET_KEY in KNOWN_KEYS or SECRET_KEY.startswith("django-insecure-") or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured(
        "SECRET_KEY is an example value or too weak. Generate one with: "
        "python3 -c 'import secrets; print(secrets.token_urlsafe(50))'"
    )
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# MySQL, PostgreSQL or another engine, from one URL. MySQL gets utf8mb4.
DATABASES = {"default": database_config(env.str("DATABASE_URL"))}

MEDIA_ROOT = Path(env.str("MEDIA_ROOT"))
STATIC_ROOT = Path(env.str("STATIC_ROOT"))
# Off behind our own Nginx (deploy/setup.sh), which serves the website, console, /static/ and /media/.
# On for production on a hosting panel (deploy/qa/README.md): the panel's nginx serves files with an
# extension from the site folder and forwards the rest, the website's pages among them, to Django.
SERVE_FILES = env.bool("SERVE_FILES", default=False)

# Nginx terminates HTTPS and passes X-Forwarded-Proto and X-Real-IP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TRUST_X_REAL_IP = env.bool("TRUST_X_REAL_IP", default=True)
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
# Security gap G11: Django sends Strict-Transport-Security for a year on what it answers (the
# API, Django admin). Nginx sends the same header on the website and console pages it serves
# itself (deploy/setup.sh, once the certificate exists); no response carries both.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
# Nginx redirects http to https, so Django does not (W008). Subdomains and the preload list
# stay off until the owner's domain is known: they bind every subdomain to HTTPS for a year
# (W005, W021). Every other security check stops deploy.sh (STRICT_CHECKS=1).
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W005", "security.W021"]
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Citizen posting tokens only for our genuine app on a real phone (docs/api-contract.md, section 15).
CITIZEN_INTEGRITY_REQUIRED = env.bool("CITIZEN_INTEGRITY_REQUIRED", default=True)

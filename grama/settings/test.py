"""pytest: dev settings with fast password hashing and synchronous jobs.

pytest-django runs every test against a fresh in-memory SQLite database,
Django's default engine.
"""

from .dev import *  # noqa: F403
from .dev import BASE_DIR, Q_CLUSTER

# Fast hashing for tests; production uses Argon2 (base.py).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Background jobs run inline, so tests need no qcluster.
Q_CLUSTER = {**Q_CLUSTER, "sync": True}

MEDIA_ROOT = BASE_DIR / ".test-media"

# Nothing sends email; locmem keeps any stray message out of the console.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Pushes are kept in notifications.push.LocmemPushGateway.outbox; retries do not wait.
PUSH_GATEWAY = "apps.notifications.push.LocmemPushGateway"
# Never call Google from tests; test_transliterate.py swaps in LocmemTransliterator.
TRANSLITERATION_PROVIDER = "off"
DELIVERY_RETRY_BASE_SECONDS = 0

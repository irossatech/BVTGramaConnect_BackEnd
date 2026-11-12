"""A post's public link: https://<domain>/p/<share_code>/ (contract section 6, "Sharing").

The code is 8 random small letters and digits, made the first time a post is published, in the
same UPDATE that publishes it (services.py), and never changed afterwards. Small letters only, so
every database compares codes the same way: MySQL's usual collation ignores case, and would treat
"aB3…" and "Ab3…" as one code (audit finding BE-DB-020). 36**8 codes make a clash practically
impossible; the unique column is the real guard, and publish_with_share_code tries again on a clash.
Older mixed-case codes stay valid.
"""

from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce

ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 8
#: Fresh codes tried when the new one is already taken, before giving up.
CODE_TRIES = 3


def new_share_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def share_code_once():
    """For an UPDATE that publishes: keep the post's code, or give it a new one."""
    return Coalesce(F("share_code"), Value(new_share_code()))


def publish_with_share_code(queryset, **values) -> int:
    """queryset.update(**values, share_code=share_code_once()), with a fresh code if the new one is taken.

    Each try runs in a savepoint, so a clash does not spoil the caller's transaction.
    Returns the number of rows updated, as update() does.
    """
    for attempt in range(CODE_TRIES):
        try:
            with transaction.atomic():
                return queryset.update(**values, share_code=share_code_once())
        except IntegrityError:
            if attempt == CODE_TRIES - 1:
                raise
    return 0  # not reached


def link(path: str) -> str | None:
    """PUBLIC_BASE_URL + path, or None while the public address is not set."""
    return f"{settings.PUBLIC_BASE_URL}{path}" if settings.PUBLIC_BASE_URL else None


def share_url(post) -> str | None:
    """The link to share for a published post; None for any other status, so the app shares the app link."""
    from .models import PostStatus

    if post.status != PostStatus.PUBLISHED or not post.share_code:
        return None
    return link(f"p/{post.share_code}/")


def invite_url() -> str | None:
    return link("app/")

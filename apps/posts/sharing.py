"""A post's public link: https://<domain>/p/<share_code>/ (contract section 6, "Sharing").

The code is 8 random letters and digits, made the first time a post is published, in the same
UPDATE that publishes it (services.py), and never changed afterwards. 62**8 codes make a clash
practically impossible, and the unique column is the real guard.
"""

from __future__ import annotations

import secrets
import string

from django.conf import settings
from django.db.models import F, Value
from django.db.models.functions import Coalesce

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 8


def new_share_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def share_code_once():
    """For an UPDATE that publishes: keep the post's code, or give it a new one."""
    return Coalesce(F("share_code"), Value(new_share_code()))


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

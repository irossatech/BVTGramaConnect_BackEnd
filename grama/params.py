"""Reading ids and numbers from URLs and query strings, so bad input is a 400 or 404, never a 500.

str.isdigit() also accepts characters such as "²" that int() cannot read, and the ORM raises when a
UUID column is compared with text that is not a UUID (audit finding BE-ERR-101).
"""

from __future__ import annotations

import uuid

#: A UUID as Django's <uuid:> path converter accepts it, in either case. For router lookup_value_regex.
UUID_REGEX = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def is_number(value) -> bool:
    """True when int(value) works on the text: decimal digits only (Telugu digits too), never "²" or "-1"."""
    return isinstance(value, str) and value.isdecimal()


def uuid_or_none(value) -> uuid.UUID | None:
    """The UUID in `value`, or None when it is not one."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None

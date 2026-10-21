"""The console's guards are permissions now: see apps/accounts/permissions.py (HasPerm).

client_via stays importable from here for the console API modules.
"""

from apps.accounts.authentication import client_via  # noqa: F401

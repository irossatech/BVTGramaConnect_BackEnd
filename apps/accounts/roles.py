"""Group keys.

The keys (SUPER_ADMIN, DISTRICT_ADMIN, ...) are what the API sends as "role"
and what posts and audit entries record. They come from role_matrix.ROLE_GROUPS.
They are for display, input and records only: access is decided by
permissions in authz.py, never by comparing keys.
"""

from django.db import models

from .role_matrix import ROLE_GROUPS

Role = models.TextChoices("Role", [(key, (key, spec["name"])) for key, spec in ROLE_GROUPS.items()])

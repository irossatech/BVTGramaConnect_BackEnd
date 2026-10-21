"""Group keys and display labels.

The keys (SUPER_ADMIN, DISTRICT_ADMIN, ...) are what the API sends as "role"
and what posts and audit entries record. They come from role_matrix.ROLE_GROUPS.
They are for display, input and records only: access is decided by
permissions in authz.py, never by comparing keys.
"""

from django.db import models

from .role_matrix import ROLE_GROUPS

Role = models.TextChoices("Role", [(key, (key, spec["name"])) for key, spec in ROLE_GROUPS.items()])

#: Highest first, for sorting lists by rank.
ROLE_ORDER = sorted(ROLE_GROUPS, key=lambda key: ROLE_GROUPS[key]["rank"])

ROLE_LABELS = {
    "te": {key: spec["name_te"] for key, spec in ROLE_GROUPS.items()},
    "en": {key: spec["name"] for key, spec in ROLE_GROUPS.items()},
}


def role_label(role: str, language: str = "te") -> str:
    labels = ROLE_LABELS["te" if (language or "te").startswith("te") else "en"]
    return labels.get(role, role)


def sort_roles(roles) -> list[str]:
    return sorted(roles, key=lambda key: ROLE_ORDER.index(key) if key in ROLE_ORDER else len(ROLE_ORDER))

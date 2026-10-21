"""Roles and what each may do. CLAUDE.md "Roles" is the source of these rules."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geography.models import AreaLevel


class Role(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", _("Super Admin")
    DISTRICT_ADMIN = "DISTRICT_ADMIN", _("District Admin")
    MANDAL_ADMIN = "MANDAL_ADMIN", _("Mandal Admin")
    VILLAGE_ADMIN = "VILLAGE_ADMIN", _("Village Admin")
    PANCHAYAT_MEMBER = "PANCHAYAT_MEMBER", _("Panchayat Member")


#: Highest first. Used to sort role lists.
ROLE_ORDER = [
    Role.SUPER_ADMIN,
    Role.DISTRICT_ADMIN,
    Role.MANDAL_ADMIN,
    Role.VILLAGE_ADMIN,
    Role.PANCHAYAT_MEMBER,
]

#: The area level each role is assigned at.
ROLE_LEVEL = {
    Role.SUPER_ADMIN: AreaLevel.STATE,
    Role.DISTRICT_ADMIN: AreaLevel.DISTRICT,
    Role.MANDAL_ADMIN: AreaLevel.MANDAL,
    Role.VILLAGE_ADMIN: AreaLevel.VILLAGE,
    Role.PANCHAYAT_MEMBER: AreaLevel.VILLAGE,
}

#: Roles each role may create, block, unblock and reassign, inside its area.
MANAGEABLE_ROLES = {
    Role.SUPER_ADMIN: {Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.DISTRICT_ADMIN: {Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.MANDAL_ADMIN: {Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER},
    Role.VILLAGE_ADMIN: {Role.PANCHAYAT_MEMBER},
    Role.PANCHAYAT_MEMBER: set(),
}

REVIEW_ROLES = {Role.SUPER_ADMIN, Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN}
#: Roles that may post. SUPER_ADMIN joins them only when SUPER_ADMIN_CAN_POST is on.
POSTING_ROLES = {Role.DISTRICT_ADMIN, Role.MANDAL_ADMIN, Role.VILLAGE_ADMIN, Role.PANCHAYAT_MEMBER}

ROLE_LABELS = {
    "te": {
        Role.SUPER_ADMIN: "సూపర్ అడ్మిన్",
        Role.DISTRICT_ADMIN: "జిల్లా అడ్మిన్",
        Role.MANDAL_ADMIN: "మండల అడ్మిన్",
        Role.VILLAGE_ADMIN: "గ్రామ అడ్మిన్",
        Role.PANCHAYAT_MEMBER: "పంచాయతీ సభ్యుడు",
    },
    "en": {role: str(role.label) for role in Role},
}


def role_label(role: str, language: str = "te") -> str:
    labels = ROLE_LABELS["te" if (language or "te").startswith("te") else "en"]
    return labels.get(role, role)


def sort_roles(roles) -> list[str]:
    return sorted(roles, key=ROLE_ORDER.index)

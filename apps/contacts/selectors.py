"""Read-side queries for important numbers.

    contacts_for_village(village_id)  numbers a citizen of that village sees
    manageable_contacts(user)         area numbers inside the user's areas (admin list)
    statewide_helplines(state_id)     the state's helplines (admin list and site-config)
    grouped(rows)                     the citizen's groups: one per type, "Other" per typed name
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.accounts import authz
from apps.geography.areas import DEPTH, AreaRef, ancestry
from apps.geography.models import AreaLevel

from .models import Contact, ContactSection

PERM = "contacts.manage_contact"


def _shown() -> QuerySet:
    return Contact.objects.filter(is_active=True, type__is_active=True).select_related("type")


def contacts_for_village(village_id: int) -> list[Contact]:
    """Active numbers whose area is the village, its mandal, its district or its state."""
    chain = Q(pk__in=[])
    for level, area_id in ancestry(AreaRef(AreaLevel.VILLAGE, village_id)).items():
        chain |= Q(area_level=level, area_id=area_id)
    return list(_shown().filter(chain))


def statewide_helplines(state_id: int | None) -> QuerySet:
    if state_id is None:
        return Contact.objects.none()
    return _shown().filter(area_level=AreaLevel.STATE, area_id=state_id, type__section=ContactSection.HELPLINE)


def manageable_contacts(user) -> QuerySet:
    """Area numbers inside an area where the user holds the permission. Lists filter in SQL."""
    return Contact.objects.filter(
        authz.area_q(user, PERM), is_active=True, type__section=ContactSection.AREA
    ).select_related("type", "created_by")


def sort_key(contact: Contact) -> tuple:
    """Inside a group: the most local number first, then its own order, then its name."""
    return (-DEPTH[contact.area_level], contact.sort_order, contact.name.casefold())


def grouped(rows: list[Contact]) -> list[dict]:
    """[{"type", "label", "contacts": [Contact, …]}] in the types' order; empty groups never appear."""
    groups: dict[tuple, dict] = {}
    for contact in rows:
        custom = contact.custom_type.casefold() if contact.type.is_other else ""
        key = (contact.type.id, custom)
        if key not in groups:
            groups[key] = {
                "type": contact.type.key,
                "label": contact.label(),
                "order": (contact.type.sort_order, contact.type.id, custom),
                "contacts": [],
            }
        groups[key]["contacts"].append(contact)
    ordered = sorted(groups.values(), key=lambda group: group["order"])
    for group in ordered:
        group["contacts"].sort(key=sort_key)
        del group["order"]
    return ordered

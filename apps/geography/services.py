"""The only code that writes districts, mandals and villages. The views and the CSV importer call it.

    create_area(actor, "VILLAGE", {"mandal_id": 12, "name_en": …}, via="console")
    update_area(actor, "VILLAGE", 77, {"pin_code": "522212"}, via="console")

Each checks the parent and the sibling names, writes and audits (old and new
values; areas hold no personal data) in one transaction, and returns the
soft warnings of validation.py. The database's unique constraints are the
real guard against two admins adding the same name at once: a lost race
answers 409 like any other duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from rest_framework import status

from apps.audit.services import record
from grama.exceptions import ApiError

from .areas import AreaRef
from .models import District, Mandal, State, Village, name_key
from .validation import warnings_for

# level -> (model, parent field, parent model, request key for the parent)
LEVELS = {
    "DISTRICT": (District, "state", State, "state_id"),
    "MANDAL": (Mandal, "district", District, "district_id"),
    "VILLAGE": (Village, "mandal", Mandal, "mandal_id"),
}
AUDITED_FIELDS = ("name_en", "name_te", "lgd_code", "is_active", "latitude", "longitude", "pin_code")


@dataclass(frozen=True)
class Outcome:
    area: object
    warnings: list
    changed: bool = True


def _duplicate(field: str, message) -> ApiError:
    return ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={field: [message]})


def _check_unique(model, parent_field, parent_id, name, lgd, exclude_id=None) -> None:
    """A friendly 409 before the write. The database constraint catches what this misses."""
    if name:
        siblings = model.objects.filter(**{f"{parent_field}_id": parent_id}, name_key=name_key(name))
        if exclude_id:
            siblings = siblings.exclude(id=exclude_id)
        if siblings.exists():
            raise _duplicate("name_en", _("This name already exists here."))
    if lgd:
        clash = model.objects.filter(lgd_code=lgd)
        if exclude_id:
            clash = clash.exclude(id=exclude_id)
        if clash.exists():
            raise _duplicate("lgd_code", _("This LGD code is already used."))


def _snapshot(obj) -> dict:
    return {
        name: (None if getattr(obj, name) is None else str(getattr(obj, name)))
        for name in AUDITED_FIELDS
        if hasattr(obj, name)
    }


def _current(obj) -> dict:
    """The area's values as real types (Decimal coordinates), for the warnings."""
    return {name: getattr(obj, name) for name in AUDITED_FIELDS if hasattr(obj, name)}


def create_area(actor, level: str, values: dict, *, via: str, audit: bool = True) -> Outcome:
    model, parent_field, parent_model, parent_key = LEVELS[level]
    values = dict(values)
    parent = parent_model.objects.filter(id=values.pop(parent_key, None)).first()
    if parent is None:
        raise ApiError("validation_error", _("Choose the parent area."), fields={parent_key: [_("No such area.")]})
    _check_unique(model, parent_field, parent.id, values.get("name_en"), values.get("lgd_code"))
    try:
        with transaction.atomic():
            obj = model.objects.create(**{parent_field: parent}, **values)
            warns = warnings_for(level, _current(obj), parent)
            if audit:
                record(
                    "area.created", actor=actor, target=obj, target_label=obj.name_en,
                    area=AreaRef(obj.level, obj.id), via=via,
                    data={"parent": [parent.level, parent.id], "fields": _snapshot(obj),
                          "warnings": [w["code"] for w in warns]},
                )
    except IntegrityError:  # another admin added the same name a moment earlier
        raise _duplicate("name_en", _("This name already exists here.")) from None
    return Outcome(obj, warns)


def update_area(actor, level: str, area_id: int, values: dict, *, via: str, audit: bool = True) -> Outcome:
    model, parent_field, _parent_model, _key = LEVELS[level]
    obj = model.objects.filter(id=area_id).select_related(parent_field).first()
    if obj is None:
        raise ApiError("not_found", _("No such area."), status=status.HTTP_404_NOT_FOUND)
    _check_unique(
        model, parent_field, getattr(obj, f"{parent_field}_id"), values.get("name_en"), values.get("lgd_code"), obj.id
    )
    before = _snapshot(obj)
    was_active = obj.is_active
    for key, value in values.items():
        setattr(obj, key, value)
    try:
        with transaction.atomic():
            obj.save()
            after = _snapshot(obj)
            changes = {name: [before[name], after[name]] for name in after if before.get(name) != after[name]}
            if obj.is_active != was_active:
                action = "area.reactivated" if obj.is_active else "area.deactivated"
            else:
                action = "area.updated"
            if changes and audit:
                record(
                    action, actor=actor, target=obj, target_label=obj.name_en,
                    area=AreaRef(obj.level, obj.id), via=via, data={"changes": changes},
                )
    except IntegrityError:
        raise _duplicate("name_en", _("This name already exists here.")) from None
    return Outcome(obj, warnings_for(level, _current(obj), getattr(obj, parent_field)), changed=bool(changes))

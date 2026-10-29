"""The only code that writes districts, mandals and villages. The views and the CSV importer call it.

    create_area(actor, "VILLAGE", {"mandal_id": 12, "name_en": …}, via="console")
    update_area(actor, "VILLAGE", 77, {"pin_code": "522212"}, via="console")
    import_villages([(None, mandal, {"name_en": …}), (village, mandal, {"pin_code": …})])

Each checks the parent and the sibling names, writes and audits (old and new
values; areas hold no personal data) in one transaction, and returns the
soft warnings of validation.py. The database's unique constraints are the
real guard against two admins adding the same name at once: a lost race
answers 409 like any other duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.audit.services import record
from grama.exceptions import ApiError

from .areas import AreaRef
from .models import District, Mandal, State, Village, clean_name, name_key
from .validation import warnings_for

# level -> (model, parent field, parent model, request key for the parent)
LEVELS = {
    "DISTRICT": (District, "state", State, "state_id"),
    "MANDAL": (Mandal, "district", District, "district_id"),
    "VILLAGE": (Village, "mandal", Mandal, "mandal_id"),
}
AUDITED_FIELDS = ("name_en", "name_te", "lgd_code", "is_active", "latitude", "longitude", "pin_code", "census_name")
# Rows per statement in import_villages (Django lowers them where the database needs). An UPDATE is
# one CASE per field over its whole batch, so it costs batch² and stays smaller.
BULK_CREATE_BATCH = 1000
BULK_UPDATE_BATCH = 200


@dataclass(frozen=True)
class Outcome:
    area: object
    warnings: list
    changed: bool = True


def _duplicate(field: str, message) -> ApiError:
    return ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={field: [message]})


def _check_unique(model, parent_field, parent_id, name, lgd, exclude_id=None, name_te="") -> None:
    """A friendly 409 before the write. The database constraint catches what this misses.

    The Telugu name is checked only when it is being set, and only here (no constraint): a
    duplicate already in the data must not block unrelated edits of the area until it is fixed.
    """
    siblings = model.objects.filter(**{f"{parent_field}_id": parent_id})
    if exclude_id:
        siblings = siblings.exclude(id=exclude_id)
    if name and siblings.filter(name_key=name_key(name)).exists():
        raise _duplicate("name_en", _("This name already exists here."))
    if clean_name(name_te) and siblings.filter(name_te=clean_name(name_te)).exists():
        raise _duplicate("name_te", _("This Telugu name already exists here."))
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
    _check_unique(model, parent_field, parent.id, values.get("name_en"), values.get("lgd_code"),
                  name_te=values.get("name_te", ""))
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
        model, parent_field, getattr(obj, f"{parent_field}_id"), values.get("name_en"), values.get("lgd_code"), obj.id,
        name_te=values.get("name_te", ""),
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


def import_villages(rows: list[tuple[Village | None, Mandal, dict]]) -> list[Outcome | ApiError]:
    """Create and update many villages at once, for the CSV import.

    rows: (village, mandal, values) with village None for a new one. The same
    checks, stored values and warnings as create_area and update_area, but in
    a few queries for the whole file instead of several per village. No audit:
    the import audits itself once. Answers one Outcome or ApiError per row, in
    order; if any row has an error, nothing is written.
    """
    mandal_ids = {mandal.id for _village, mandal, _values in rows}
    taken_names = {
        (mandal_id, key): pk
        for pk, mandal_id, key in Village.objects.filter(mandal_id__in=mandal_ids).values_list("id", "mandal_id", "name_key")
    }
    codes = {values["lgd_code"] for _village, _mandal, values in rows if values.get("lgd_code")}
    taken_codes = dict(Village.objects.filter(lgd_code__in=codes).values_list("lgd_code", "id"))

    results: list[Outcome | ApiError] = []
    new, changed, fields = [], [], {"updated_at"}
    now = timezone.now()
    for village, mandal, values in rows:
        if village is None:
            village = Village(mandal=mandal, **values)
        else:
            for key, value in values.items():
                setattr(village, key, value)
            village.updated_at = now
            fields.update(values)
            if "name_en" in values:
                fields.add("name_key")
        village.normalize()
        if "name_en" in values and taken_names.get((mandal.id, village.name_key), village.pk) != village.pk:
            results.append(_duplicate("name_en", _("This name already exists here.")))
            continue
        if values.get("lgd_code") and taken_codes.get(village.lgd_code, village.pk) != village.pk:
            results.append(_duplicate("lgd_code", _("This LGD code is already used.")))
            continue
        (changed if village.pk else new).append(village)
        results.append(Outcome(village, warnings_for("VILLAGE", _current(village), mandal)))

    if any(isinstance(result, ApiError) for result in results):
        return results
    try:
        with transaction.atomic():
            if changed:
                Village.objects.bulk_update(changed, sorted(fields), batch_size=BULK_UPDATE_BATCH)
            if new:
                Village.objects.bulk_create(new, batch_size=BULK_CREATE_BATCH)
    except IntegrityError:  # another admin added the same name a moment earlier
        raise _duplicate("name_en", _("This name already exists here.")) from None
    return results

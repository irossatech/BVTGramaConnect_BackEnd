"""The only code that writes important numbers and their types.

Each function checks the permission and the area, validates, writes and
audits inside one transaction, like posts/services.py does for post status.

    create_contact(actor, data, via="app")
    update_contact(actor, contact_id, data, via="app")
    remove_contact(actor, contact_id, via="app")       # soft delete
    import_contacts(actor, text, dry_run=..., via=...)  # all rows or none
    create_type(actor, data) / update_type(actor, contact_type, data)
    delete_type(actor, type_id)                         # soft delete, only when no active number uses it

Rules the model does not express (docs/api-contract.md, section 14):
section and level agree (HELPLINE at STATE, AREA below it), the area exists
and is active, no two active numbers share type, phone and area, at most
CONTACTS_MAX_PER_AREA active numbers per area, custom_type only for "Other"
types, and a 10-digit mobile needs the consent tick.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status

from apps.accounts import authz
from apps.audit.services import record
from apps.geography.areas import AreaRef, area_exists
from apps.geography.models import AreaLevel, District, Mandal, State, Village, clean_name, name_key
from grama.exceptions import ApiError, out_of_area

from .models import Contact, ContactSection, ContactType, PhoneKind
from .phones import InvalidPhone, mask, normalise_phone

PERM = "contacts.manage_contact"
TYPES_PERM = "contacts.manage_contact_types"
FIELDS = ("type", "custom_type", "name", "name_te", "phone", "email", "area", "sort_order", "consent_recorded")


def invalid(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


def default_state() -> State | None:
    return State.objects.active().order_by("id").first()


# --- Checks shared by the API and the CSV import ------------------------------------------------


def active_type(key: str, *, lock: bool = False) -> ContactType:
    """An active, not deleted type. lock=True (inside a transaction) holds its row until commit,
    so a delete_type running at the same moment waits for this write, or this write sees the delete."""
    types = ContactType.objects.live().filter(key=(key or "").strip(), is_active=True)
    contact_type = (types.select_for_update() if lock else types).first()
    if contact_type is None:
        raise invalid("type", _("Unknown contact type."))
    return contact_type


def check_section_level(contact_type: ContactType, area: AreaRef) -> None:
    if contact_type.section == ContactSection.HELPLINE and area.level != AreaLevel.STATE:
        raise invalid("area", _("Government helplines are for the whole state."))
    if contact_type.section == ContactSection.AREA and area.level == AreaLevel.STATE:
        raise invalid("area", _("Pick a district, mandal or village; state numbers are helplines."))


def check_area(area: AreaRef) -> None:
    if not area_exists(area, active_only=True):
        raise invalid("area", _("No such active area."))


def clean_contact_name(value: str, field_name: str = "name", *, required: bool = True) -> str:
    value = clean_name(value)
    if required and not value:
        raise invalid(field_name, _("Enter a name."))
    if len(value) > 150:
        raise invalid(field_name, _("At most 150 characters."))
    return value


def clean_custom_type(contact_type: ContactType, value: str) -> str:
    value = clean_name(value)
    if contact_type.is_other:
        if not value:
            raise invalid("custom_type", _("Type a name for this kind of number."))
        if len(value) > 40:
            raise invalid("custom_type", _("At most 40 characters."))
        return value
    if value:
        raise invalid("custom_type", _("Only numbers of type Other take their own type name."))
    return ""


def clean_email(value: str) -> str:
    value = (value or "").strip().lower()
    if value:
        try:
            validate_email(value)
        except DjangoValidationError:
            raise invalid("email", _("Enter a valid email address.")) from None
        if len(value) > 254:
            raise invalid("email", _("At most 254 characters."))
    return value


def clean_phone(contact_type: ContactType, raw: str) -> tuple[str, str]:
    try:
        return normalise_phone(raw, allow_short=contact_type.section == ContactSection.HELPLINE)
    except InvalidPhone as error:
        raise invalid("phone", str(error)) from None


def check_consent(kind: str, consent: bool) -> None:
    if kind == PhoneKind.MOBILE and settings.CONTACTS_REQUIRE_MOBILE_CONSENT and not consent:
        raise invalid(
            "consent_recorded",
            _("Tick that this person agreed to have their mobile number shown to the public."),
        )


def check_sort_order(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000:
        raise invalid("sort_order", _("A number from 0 to 1000."))
    return value


def check_duplicate(contact_type: ContactType, phone: str, area: AreaRef, exclude_id=None) -> None:
    rows = Contact.objects.filter(
        is_active=True, type=contact_type, phone=phone, area_level=area.level, area_id=area.id
    )
    if exclude_id is not None:
        rows = rows.exclude(id=exclude_id)
    if rows.exists():
        raise ApiError(
            "duplicate",
            _("This number is already listed here."),
            status=status.HTTP_409_CONFLICT,
            fields={"phone": [_("Already listed for this type and area.")]},
        )


def check_capacity(area: AreaRef, exclude_id=None, extra: int = 1) -> None:
    rows = Contact.objects.filter(is_active=True, area_level=area.level, area_id=area.id)
    if exclude_id is not None:
        rows = rows.exclude(id=exclude_id)
    if rows.count() + extra > settings.CONTACTS_MAX_PER_AREA:
        raise invalid("area", _("This area already has the most numbers allowed."))


def may_manage(actor, area: AreaRef) -> bool:
    return authz.can(actor, PERM, area)


def reachable(actor, contact_id) -> Contact:
    """An active number the actor may change, or 404 so its existence stays hidden."""
    contact = Contact.objects.select_related("type").filter(id=contact_id, is_active=True).first()
    if contact is None or not may_manage(actor, contact.area):
        raise ApiError("not_found", _("No such number."), status=status.HTTP_404_NOT_FOUND)
    return contact


def _audit_data(contact: Contact, **extra) -> dict:
    return {"type": contact.type.key, "phone": mask(contact.phone), **extra}


def _check_type_still_live(contact_type: ContactType) -> None:
    """Second guard next to the row lock: a number never ends up on a type deleted meanwhile."""
    if not ContactType.objects.live().filter(id=contact_type.id).exists():
        raise invalid("type", _("Unknown contact type."))


# --- Numbers --------------------------------------------------------------------------------------


@transaction.atomic
def create_contact(actor, data: dict, *, via: str = "app") -> Contact:
    contact_type = active_type(data.get("type", ""), lock=True)
    area = data["area"]
    check_section_level(contact_type, area)
    check_area(area)
    if not may_manage(actor, area):
        raise out_of_area(_("You can add numbers only inside your own area."))
    phone, kind = clean_phone(contact_type, data.get("phone", ""))
    custom_type = clean_custom_type(contact_type, data.get("custom_type", ""))
    name = clean_contact_name(data.get("name", ""))
    name_te = clean_contact_name(data.get("name_te", ""), "name_te", required=False)
    email = clean_email(data.get("email", ""))
    sort_order = check_sort_order(data.get("sort_order", 100))
    consent = bool(data.get("consent_recorded", False))
    check_consent(kind, consent)
    check_duplicate(contact_type, phone, area)
    check_capacity(area)
    contact = Contact.objects.create(
        type=contact_type, custom_type=custom_type, name=name, name_te=name_te, phone=phone, phone_kind=kind,
        email=email, area_level=area.level, area_id=area.id, sort_order=sort_order, consent_recorded=consent,
        verified_at=timezone.now(), created_by=actor, updated_by=actor,
    )
    _check_type_still_live(contact_type)
    record("contact.created", actor=actor, target=contact, target_label=contact.name, area=area, via=via,
           data=_audit_data(contact))
    return contact


@transaction.atomic
def update_contact(actor, contact_id, data: dict, *, via: str = "app") -> Contact:
    contact = reachable(actor, contact_id)
    old_phone = contact.phone
    values: dict = {}

    contact_type = active_type(data["type"], lock=True) if "type" in data else contact.type
    area = data.get("area", contact.area)
    if area != contact.area:
        check_area(area)
        if not may_manage(actor, area):
            raise out_of_area(_("You can move numbers only inside your own area."))
    check_section_level(contact_type, area)

    if "phone" in data or contact_type.section != contact.type.section:
        phone, kind = clean_phone(contact_type, data.get("phone", contact.phone))
    else:
        phone, kind = contact.phone, contact.phone_kind
    if "custom_type" in data or contact_type.id != contact.type_id:
        custom_type = clean_custom_type(contact_type, data.get("custom_type", "" if not contact_type.is_other else contact.custom_type))
    else:
        custom_type = contact.custom_type
    consent = bool(data.get("consent_recorded", contact.consent_recorded))
    check_consent(kind, consent)

    proposed = {
        "type_id": contact_type.id,
        "custom_type": custom_type,
        "phone": phone,
        "phone_kind": kind,
        "area_level": area.level,
        "area_id": area.id,
        "consent_recorded": consent,
    }
    if "name" in data:
        proposed["name"] = clean_contact_name(data["name"])
    if "name_te" in data:
        proposed["name_te"] = clean_contact_name(data["name_te"], "name_te", required=False)
    if "email" in data:
        proposed["email"] = clean_email(data["email"])
    if "sort_order" in data:
        proposed["sort_order"] = check_sort_order(data["sort_order"])
    for name, value in proposed.items():
        if getattr(contact, name) != value:
            values[name] = value

    if {"type_id", "phone", "area_level", "area_id"} & set(values):
        check_duplicate(contact_type, phone, area, exclude_id=contact.id)
    if {"area_level", "area_id"} & set(values):
        check_capacity(area, exclude_id=contact.id)

    changed = sorted({"type" if n == "type_id" else "area" if n in ("area_level", "area_id") else n for n in values})
    if data.get("verified"):
        values["verified_at"] = timezone.now()
        changed.append("verified")
    if not values:
        return contact
    values["updated_by"] = actor
    for name, value in values.items():
        setattr(contact, name, value)
    contact.type = contact_type
    contact.save()
    if "type_id" in values:
        _check_type_still_live(contact_type)
    extra = {"fields": changed}
    if contact.phone != old_phone:
        extra["phone"] = {"from": mask(old_phone), "to": mask(contact.phone)}
    record("contact.updated", actor=actor, target=contact, target_label=contact.name, area=contact.area, via=via,
           data={"type": contact.type.key, **extra})
    return contact


@transaction.atomic
def remove_contact(actor, contact_id, *, via: str = "app") -> None:
    contact = reachable(actor, contact_id)
    # Conditional update: two admins removing the same number at once is harmless on any database.
    removed = Contact.objects.filter(id=contact.id, is_active=True).update(
        is_active=False, removed_by=actor, removed_at=timezone.now(), updated_by=actor, updated_at=timezone.now()
    )
    if not removed:
        raise ApiError("not_found", _("No such number."), status=status.HTTP_404_NOT_FOUND)
    record("contact.removed", actor=actor, target=contact, target_label=contact.name, area=contact.area, via=via,
           data=_audit_data(contact))


# --- Types ----------------------------------------------------------------------------------------

TYPE_FIELDS = ("name_en", "name_te", "is_other", "sort_order", "is_active")


@transaction.atomic
def create_type(actor, data: dict, *, via: str = "console") -> ContactType:
    taken = ContactType.objects.filter(key=data["key"]).first()
    if taken is not None:
        # A deleted type keeps its key, so old audit entries never point to a different type.
        message = _("This key was used by a deleted type.") if taken.deleted_at else _("This key is taken.")
        raise ApiError("duplicate", message, status=status.HTTP_409_CONFLICT, fields={"key": [_("Already used.")]})
    contact_type = ContactType.objects.create(**{k: data[k] for k in ("key", "section", *TYPE_FIELDS) if k in data})
    record("contact_type.created", actor=actor, target=contact_type, target_label=contact_type.key, via=via,
           data={"key": contact_type.key, "section": contact_type.section})
    return contact_type


@transaction.atomic
def update_type(actor, contact_type: ContactType, data: dict, *, via: str = "console") -> ContactType:
    """Key and section never change: numbers already rely on them. A type in use is switched off, not deleted."""
    changed = sorted(name for name in TYPE_FIELDS if name in data and getattr(contact_type, name) != data[name])
    if changed:
        for name in changed:
            setattr(contact_type, name, data[name])
        contact_type.save()
        record("contact_type.updated", actor=actor, target=contact_type, target_label=contact_type.key, via=via,
               data={"key": contact_type.key, "fields": changed})
    return contact_type


def _active_count(contact_type: ContactType) -> int:
    return Contact.objects.filter(type=contact_type, is_active=True).count()


def _type_in_use(count: int) -> ApiError:
    return ApiError(
        "type_in_use",
        _("%(count)d numbers use this type. Change their type or remove them first, or switch the type off.")
        % {"count": count},
        status=status.HTTP_409_CONFLICT,
        count=count,
    )


@transaction.atomic
def delete_type(actor, type_id, *, via: str = "console") -> None:
    """Soft delete a type no active number uses. Removed numbers keep pointing to it (Contact.type is PROTECT).

    The row lock makes a number added at the same moment wait (or this delete wait for it); the
    conditional update and the second count keep it safe where locks are not enforced, as on SQLite.
    """
    contact_type = ContactType.objects.live().select_for_update().filter(pk=type_id).first()
    if contact_type is None:
        raise ApiError("not_found", _("No such type."), status=status.HTTP_404_NOT_FOUND)
    if contact_type.is_other:
        raise ApiError(
            "protected",
            _("The Other type cannot be deleted: the CSV import and existing Other numbers rely on it. "
              "Switch it off when no number uses it."),
            status=status.HTTP_409_CONFLICT,
        )
    count = _active_count(contact_type)
    if count:
        raise _type_in_use(count)
    now = timezone.now()
    deleted = ContactType.objects.live().filter(pk=contact_type.pk).update(
        deleted_at=now, deleted_by=actor, updated_at=now
    )
    if not deleted:
        raise ApiError("not_found", _("No such type."), status=status.HTTP_404_NOT_FOUND)
    count = _active_count(contact_type)
    if count:
        raise _type_in_use(count)  # rolls the delete back
    record("contact_type.deleted", actor=actor, target=contact_type, target_label=contact_type.key, via=via,
           data={"key": contact_type.key, "section": contact_type.section, "name_en": contact_type.name_en})


# --- CSV import -------------------------------------------------------------------------------------

REQUIRED_COLUMNS = {"type", "name", "phone"}
KNOWN_COLUMNS = REQUIRED_COLUMNS | {
    "custom_type", "name_te", "email", "sort_order", "consent",
    "district_lgd", "mandal_lgd", "village_lgd", "district", "mandal", "village",
}
TRUE = {"1", "yes", "y", "true"}


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"created": self.created, "updated": self.updated, "skipped": self.skipped, "errors": self.errors}


class _Areas:
    """Finds areas by LGD code, falling back to the name within the parent. The deepest given sets the level."""

    def __init__(self):
        self.state = default_state()

    def _find(self, model, code: str, name: str, parent_filter: dict):
        if code:
            return model.objects.filter(lgd_code=code, **parent_filter).first()
        if name:
            return model.objects.filter(name_key=name_key(name), **parent_filter).first()
        return None

    def resolve(self, row: dict, section: str) -> AreaRef:
        given = {level: (row.get(f"{level}_lgd", "").strip(), row.get(level, "").strip()) for level in ("district", "mandal", "village")}
        if section == ContactSection.HELPLINE:
            if any(code or name for code, name in given.values()):
                raise invalid("area", _("Helplines are statewide: leave the area columns empty."))
            if self.state is None:
                raise invalid("area", _("Load the state first."))
            return AreaRef(AreaLevel.STATE, self.state.id)
        district = mandal = village = None
        code, name = given["district"]
        if code or name:
            district = self._find(District, code, name, {"state": self.state} if self.state else {})
            if district is None:
                raise invalid("district", _("No such district."))
        code, name = given["mandal"]
        if code or name:
            mandal = self._find(Mandal, code, name, {"district": district} if district else {})
            if mandal is None:
                raise invalid("mandal", _("No such mandal."))
        code, name = given["village"]
        if code or name:
            village = self._find(Village, code, name, {"mandal": mandal} if mandal else {})
            if village is None:
                raise invalid("village", _("No such village."))
        deepest = village or mandal or district
        if deepest is None:
            raise invalid("area", _("Give a district, mandal or village."))
        return AreaRef(deepest.level, deepest.id)


def _read_rows(text: str, report: ImportReport) -> list[tuple[int, dict]]:
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        report.errors.append({"row": 1, "field": "header", "message": _("Missing columns: %s") % ", ".join(sorted(missing))})
        return []
    unknown = headers - KNOWN_COLUMNS
    if unknown:
        report.errors.append({"row": 1, "field": "header", "message": _("Unknown columns: %s") % ", ".join(sorted(unknown))})
        return []
    rows = []
    for line, raw in enumerate(reader, start=2):
        if len(rows) >= settings.CONTACTS_IMPORT_MAX_ROWS:
            report.errors.append({"row": line, "field": "file", "message": _("At most %d rows.") % settings.CONTACTS_IMPORT_MAX_ROWS})
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append((line, row))
    return rows


def import_contacts(actor, text: str, *, dry_run: bool = False, via: str = "console") -> ImportReport:
    """Create or update numbers from CSV: the same rules as the API. Any error writes nothing."""
    report = ImportReport()
    rows = _read_rows(text, report)
    areas = _Areas()
    planned: list[tuple[str, Contact | None, dict]] = []
    seen: set[tuple] = set()
    added_per_area: dict[AreaRef, int] = {}
    for line, row in rows:
        try:
            contact_type = active_type(row["type"])
            area = areas.resolve(row, contact_type.section)
            check_section_level(contact_type, area)
            check_area(area)
            phone, kind = clean_phone(contact_type, row["phone"])
            values = {
                "custom_type": clean_custom_type(contact_type, row.get("custom_type", "")),
                "name": clean_contact_name(row.get("name", "")),
                "name_te": clean_contact_name(row.get("name_te", ""), "name_te", required=False),
                "email": clean_email(row.get("email", "")),
                "sort_order": check_sort_order(int(row["sort_order"])) if row.get("sort_order") else 100,
                "consent_recorded": row.get("consent", "").lower() in TRUE,
            }
            check_consent(kind, values["consent_recorded"])
        except ApiError as error:
            for field_name, messages in (error.fields or {"row": [str(error.detail)]}).items():
                report.errors.append({"row": line, "field": field_name, "message": str(messages[0])})
            continue
        except ValueError:
            report.errors.append({"row": line, "field": "sort_order", "message": _("A number from 0 to 1000.")})
            continue
        key = (contact_type.id, phone, area)
        if key in seen:
            report.errors.append({"row": line, "field": "phone", "message": _("Listed twice in this file.")})
            continue
        seen.add(key)
        existing = Contact.objects.filter(
            is_active=True, type=contact_type, phone=phone, area_level=area.level, area_id=area.id
        ).first()
        values.update(type=contact_type, phone=phone, phone_kind=kind, area_level=area.level, area_id=area.id)
        if existing is None:
            added_per_area[area] = added_per_area.get(area, 0) + 1
            planned.append(("create", None, values))
        elif any(getattr(existing, name) != value for name, value in values.items() if name != "type"):
            planned.append(("update", existing, values))
        else:
            report.skipped += 1
    for area, extra in added_per_area.items():
        try:
            check_capacity(area, extra=extra)
        except ApiError as error:
            report.errors.append({"row": 0, "field": "area", "message": f"{area.level} {area.id}: {error.detail}"})
    report.created = sum(1 for action, _c, _v in planned if action == "create")
    report.updated = sum(1 for action, _c, _v in planned if action == "update")
    if not report.ok or dry_run:
        return report
    now = timezone.now()
    with transaction.atomic():
        # A type deleted or switched off since the rows were checked fails the whole import.
        type_ids = {values["type"].id for _a, _c, values in planned}
        if len(set(ContactType.objects.live().select_for_update().filter(id__in=type_ids, is_active=True)
                   .values_list("id", flat=True))) != len(type_ids):
            raise invalid("type", _("Unknown contact type."))
        for action, contact, values in planned:
            if action == "create":
                Contact.objects.create(**values, verified_at=now, created_by=actor, updated_by=actor)
            else:
                for name, value in values.items():
                    setattr(contact, name, value)
                contact.verified_at = now
                contact.updated_by = actor
                contact.save()
        record("contacts.imported", actor=actor, via=via,
               data={"created": report.created, "updated": report.updated, "skipped": report.skipped})
    return report

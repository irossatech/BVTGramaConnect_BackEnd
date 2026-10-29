"""Bills and recharges: the only writer. docs/api-contract.md, section 18.

The Super Admin adds, changes, hides and reorders bills, as with services. The key is
made from the English name on create and never changes; bills are never deleted. An
optional logo goes through grama.logos, the same pipeline as service logos; the file is
bills/<key>-<random>-256.webp. Names and keys are unique among bills only: a bill may
share a name with a service.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.audit.services import record
from grama import logos
from grama.exceptions import ApiError

from .models import Bill

#: Bill fields the Super Admin may change.
BILL_FIELDS = ("name_en", "name_te", "icon", "color", "is_active")


def _validation(field: str, message) -> ApiError:
    return ApiError("validation_error", message, fields={field: [message]})


def _new_key(name_en: str) -> str:
    base = slugify(name_en).replace("-", "_")[:36].strip("_") or "bill"
    key, n = base, 2
    while Bill.objects.filter(key=key).exists():
        key, n = f"{base}_{n}", n + 1
    return key


def _check_name(name_en: str, bill: Bill | None = None) -> None:
    taken = Bill.objects.filter(name_en__iexact=name_en)
    if bill is not None:
        taken = taken.exclude(pk=bill.pk)
    if taken.exists():
        raise _validation("name_en", _("A bill with this name already exists."))


def _delete_logo_after_commit(name: str) -> None:
    logos.delete_after_commit(name, Bill._meta.get_field("logo").storage)


def create_bill(user, values: dict, logo=None) -> Bill:
    _check_name(values["name_en"])
    data = logos.process_logo(logo) if logo is not None else None
    with transaction.atomic():
        last = Bill.objects.aggregate(last=Max("order"))["last"]
        bill = Bill.objects.create(
            key=_new_key(values["name_en"]),
            order=0 if last is None else last + 1,
            **{name: values[name] for name in BILL_FIELDS if name in values},
        )
        if data is not None:
            logos.store_logo(bill.logo, bill.key, data)
            bill.save(update_fields=["logo"])
        record("bill.created", actor=user, target=bill, target_label=bill.name_en,
               data={"key": bill.key, "logo": data is not None})
    return bill


def update_bill(user, bill: Bill, changes: dict, logo=None, remove_logo: bool = False) -> Bill:
    """Change fields; `logo` replaces the logo, `remove_logo` takes it away (the app then draws `icon`)."""
    if "name_en" in changes:
        _check_name(changes["name_en"], bill)
    data = logos.process_logo(logo) if logo is not None else None
    changed = [name for name in BILL_FIELDS if name in changes and changes[name] != getattr(bill, name)]
    drop_logo = remove_logo and data is None and bool(bill.logo)
    if not changed and data is None and not drop_logo:
        return bill
    with transaction.atomic():
        for name in changed:
            setattr(bill, name, changes[name])
        if data is not None or drop_logo:
            _delete_logo_after_commit(bill.logo.name)
            if data is not None:
                logos.store_logo(bill.logo, bill.key, data)
            else:
                bill.logo = ""
            changed.append("logo")
        bill.save()
        details = [name for name in changed if name != "is_active"]
        if details:
            record("bill.updated", actor=user, target=bill, target_label=bill.name_en,
                   data={"key": bill.key, "changed": details})
        if "is_active" in changed:
            record("bill.shown" if bill.is_active else "bill.hidden", actor=user, target=bill,
                   target_label=bill.name_en, data={"key": bill.key})
    return bill


def reorder_bills(user, keys: list[str]) -> None:
    """`keys` is every bill's key in the new order; anything else (a service key too) is 400 on `keys`."""
    existing = dict(Bill.objects.values_list("key", "order"))
    if len(keys) != len(set(keys)) or set(keys) != set(existing):
        raise _validation("keys", _("Send every bill's key exactly once."))
    with transaction.atomic():
        for position, key in enumerate(keys):
            if existing[key] != position:
                Bill.objects.filter(key=key).update(order=position, updated_at=timezone.now())
        record("bill.reordered", actor=user, data={"keys": keys})

"""Soft data-quality warnings for area writes. They never block a save.

Writes answer them in "warnings", so the app and the console can say "check
this": a missing Telugu name, swapped coordinates, a PIN code from another
state. The CSV importer adds them to its report.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _

TELUGU = range(0x0C00, 0x0C80)


def has_telugu(text: str) -> bool:
    return any(ord(ch) in TELUGU for ch in text)


def warnings_for(level: str, values: dict, parent) -> list[dict]:
    """values: the area's fields after the write (name_te, latitude, longitude, pin_code …)."""
    out = []

    def warn(code, message):
        out.append({"code": code, "message": message})

    if parent is not None and not parent.is_active:
        warn("parent_inactive", _("The parent area is switched off, so this one stays hidden until it is switched on."))
    te = (values.get("name_te") or "").strip()
    if not te or te == (values.get("name_en") or "").strip():
        warn("name_te_missing", _("No Telugu name: the English name is shown on Telugu screens."))
    elif not has_telugu(te):
        warn("name_te_not_telugu", _("The Telugu name has no Telugu letters."))
    if level == "VILLAGE":
        lat, lng = values.get("latitude"), values.get("longitude")
        if lat is not None and lng is not None:
            bounds = settings.GEO_BOUNDS
            inside = bounds["lat"][0] <= lat <= bounds["lat"][1] and bounds["lng"][0] <= lng <= bounds["lng"][1]
            swapped = bounds["lat"][0] <= lng <= bounds["lat"][1] and bounds["lng"][0] <= lat <= bounds["lng"][1]
            if swapped:
                warn("coordinates_swapped", _("Latitude and longitude look swapped."))
            elif not inside:
                warn("outside_state", _("These coordinates are outside Andhra Pradesh."))
        pin = values.get("pin_code") or ""
        if pin and not pin.startswith("5"):
            warn("pin_outside_state", _("Andhra Pradesh PIN codes start with 5."))
    return out

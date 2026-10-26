"""Import the State > District > Mandal > Village hierarchy from a CSV file.

One row per village. Required columns: state, district, mandal, village.
Optional columns: state_te, district_te, mandal_te, village_te, latitude,
longitude, pin_code, district_lgd_code, mandal_lgd_code, village_lgd_code.
Common header variants (lat, lng, lon, pincode, "Village Name", …) are accepted.

Rules:
- Everything is validated before anything is written. Any error means no change.
- Running the same file twice changes nothing the second time.
- An area is matched by its LGD code when the file gives one, otherwise by
  its English name inside its parent (ignoring case and extra spaces).
- Blank cells never erase data already in the database.
- Nothing is deleted or deactivated; rows missing from the file are left alone.
- Districts, mandals and villages are written through services.py, the same
  writer as the API, without one audit entry per row: the view audits the
  whole import once (area.imported). The writer's data-quality warnings join
  the report, except a missing Telugu name, which missing_telugu counts.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from grama.exceptions import ApiError

from . import services
from .models import District, Mandal, State, Village, clean_name, name_key

REQUIRED = ("state", "district", "mandal", "village")
OPTIONAL = (
    "state_te",
    "district_te",
    "mandal_te",
    "village_te",
    "latitude",
    "longitude",
    "pin_code",
    "district_lgd_code",
    "mandal_lgd_code",
    "village_lgd_code",
)
ALIASES = {
    "state_name": "state",
    "district_name": "district",
    "mandal_name": "mandal",
    "village_name": "village",
    "lat": "latitude",
    "lng": "longitude",
    "lon": "longitude",
    "long": "longitude",
    "pincode": "pin_code",
    "pin": "pin_code",
}
SIX_PLACES = Decimal("0.000001")
# Rough box around India, used only to warn about likely data-entry mistakes.
INDIA_LAT = (Decimal("6"), Decimal("37.5"))
INDIA_LNG = (Decimal("68"), Decimal("97.5"))
LEVELS = ("state", "district", "mandal", "village")


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class ImportReport:
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, Counts] = field(default_factory=lambda: {level: Counts() for level in LEVELS})
    #: (state, district, mandal, village, village_lgd_code) of villages in
    #: the file that still have no coordinates after the import.
    missing_coordinates: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    missing_telugu: int = 0
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Node:
    """One area as described by the file, before it is matched to the database."""

    level: str
    name_en: str
    parent: Node | None
    name_te: str = ""
    lgd_code: str = ""
    lines: list[int] = field(default_factory=list)
    # Villages only.
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    pin_code: str = ""
    obj: object = None

    @property
    def key(self) -> tuple:
        parent_key = self.parent.key if self.parent else ()
        return (*parent_key, name_key(self.name_en))

    def label(self) -> str:
        names = []
        node = self
        while node:
            names.append(node.name_en)
            node = node.parent
        return " > ".join(reversed(names))


def _header(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return ALIASES.get(key, key)


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.strip()).quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


class GeographyImporter:
    def __init__(self, *, dry_run: bool = False, actor=None, via: str = "system"):
        self.report = ImportReport(dry_run=dry_run)
        self.actor = actor
        self.via = via
        self.states: dict[tuple, Node] = {}
        self.districts: dict[tuple, Node] = {}
        self.mandals: dict[tuple, Node] = {}
        self.villages: dict[tuple, Node] = {}
        self.lgd_seen: dict[tuple[str, str], Node] = {}

    # --- Reading and validating the file ------------------------------------

    def run_file(self, path: str | Path) -> ImportReport:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return self.run(handle)

    def run_text(self, text: str) -> ImportReport:
        return self.run(io.StringIO(text))

    def run(self, handle) -> ImportReport:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            self.report.errors.append("The file is empty.")
            return self.report
        columns = {_header(name): name for name in reader.fieldnames if name}
        missing = [name for name in REQUIRED if name not in columns]
        if missing:
            self.report.errors.append(
                "Missing required column(s): " + ", ".join(missing)
                + ". Found: " + ", ".join(reader.fieldnames)
            )
            return self.report
        unknown = sorted(set(columns) - set(REQUIRED) - set(OPTIONAL))
        if unknown:
            self.report.warnings.append("Ignored column(s): " + ", ".join(unknown))

        for line, raw in enumerate(reader, start=2):
            row = {key: (raw.get(original) or "").strip() for key, original in columns.items()}
            if not any(row.values()):
                continue
            self.report.rows += 1
            self._read_row(line, row)

        if self.report.rows == 0:
            self.report.errors.append("The file has a header but no data rows.")
        if self.report.ok:
            self._match_database()
        if self.report.ok:
            self._write()
        return self.report

    def _error(self, line: int, message: str) -> None:
        self.report.errors.append(f"Line {line}: {message}")

    def _warn(self, line: int, message: str) -> None:
        self.report.warnings.append(f"Line {line}: {message}")

    def _node(self, store, level, line, name, parent, name_te="", lgd_code="") -> Node:
        node = Node(level=level, name_en=clean_name(name), parent=parent)
        existing = store.get(node.key)
        if existing is None:
            store[node.key] = node
            existing = node
        existing.lines.append(line)

        name_te = clean_name(name_te)
        if name_te:
            if existing.name_te and existing.name_te != name_te:
                self._error(
                    line,
                    f"{level} '{existing.label()}' has Telugu name {name_te!r} here "
                    f"but {existing.name_te!r} on line {existing.lines[0]}.",
                )
            else:
                existing.name_te = name_te

        lgd_code = (lgd_code or "").strip()
        if lgd_code:
            if existing.lgd_code and existing.lgd_code != lgd_code:
                self._error(
                    line,
                    f"{level} '{existing.label()}' has LGD code {lgd_code} here "
                    f"but {existing.lgd_code} on line {existing.lines[0]}.",
                )
            else:
                owner = self.lgd_seen.get((level, lgd_code))
                if owner is not None and owner is not existing:
                    self._error(
                        line,
                        f"LGD code {lgd_code} is used for {level} '{existing.label()}' "
                        f"and for '{owner.label()}' (line {owner.lines[0]}).",
                    )
                else:
                    self.lgd_seen[(level, lgd_code)] = existing
                    existing.lgd_code = lgd_code
        return existing

    def _read_row(self, line: int, row: dict[str, str]) -> None:
        blanks = [name for name in REQUIRED if not row.get(name)]
        if blanks:
            self._error(line, "empty " + ", ".join(blanks) + ".")
            return

        state = self._node(self.states, "state", line, row["state"], None, row.get("state_te"))
        district = self._node(
            self.districts, "district", line, row["district"], state,
            row.get("district_te"), row.get("district_lgd_code"),
        )
        mandal = self._node(
            self.mandals, "mandal", line, row["mandal"], district,
            row.get("mandal_te"), row.get("mandal_lgd_code"),
        )

        key = (*mandal.key, name_key(row["village"]))
        if key in self.villages:
            first = self.villages[key].lines[0]
            self._error(line, f"village '{self.villages[key].label()}' is listed again (first on line {first}).")
            return
        village = self._node(
            self.villages, "village", line, row["village"], mandal,
            row.get("village_te"), row.get("village_lgd_code"),
        )

        lat_raw, lng_raw = row.get("latitude", ""), row.get("longitude", "")
        if lat_raw or lng_raw:
            if not (lat_raw and lng_raw):
                self._error(line, "give both latitude and longitude, or neither.")
            else:
                try:
                    lat, lng = _decimal(lat_raw), _decimal(lng_raw)
                except InvalidOperation:
                    self._error(line, f"latitude {lat_raw!r} or longitude {lng_raw!r} is not a number.")
                else:
                    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                        self._error(line, f"coordinates {lat}, {lng} are out of range.")
                    else:
                        in_india = INDIA_LAT[0] <= lat <= INDIA_LAT[1] and INDIA_LNG[0] <= lng <= INDIA_LNG[1]
                        swapped = INDIA_LAT[0] <= lng <= INDIA_LAT[1] and INDIA_LNG[0] <= lat <= INDIA_LNG[1]
                        if not in_india:
                            hint = " Latitude and longitude look swapped." if swapped else ""
                            self._warn(line, f"coordinates {lat}, {lng} are outside India.{hint}")
                        village.latitude, village.longitude = lat, lng

        pin = row.get("pin_code", "")
        if pin:
            if re.fullmatch(r"\d{6}", pin):
                village.pin_code = pin
            else:
                self._error(line, f"PIN code {pin!r} must be 6 digits.")

    # --- Matching against the database ----------------------------------------

    def _match_database(self) -> None:
        state_by_key = {s.name_key: s for s in State.objects.all()}
        for node in self.states.values():
            node.obj = state_by_key.get(name_key(node.name_en))

        self._match_level(
            self.districts.values(), District, parent_field="state_id",
        )
        self._match_level(
            self.mandals.values(), Mandal, parent_field="district_id",
        )
        self._match_level(
            self.villages.values(), Village, parent_field="mandal_id",
        )

    def _match_level(self, nodes, model, *, parent_field: str) -> None:
        nodes = list(nodes)
        parent_ids = {node.parent.obj.id for node in nodes if node.parent.obj is not None}
        codes = {node.lgd_code for node in nodes if node.lgd_code}
        by_code = {obj.lgd_code: obj for obj in model.objects.filter(lgd_code__in=codes)}
        by_name = {
            (getattr(obj, parent_field), obj.name_key): obj
            for obj in model.objects.filter(**{f"{parent_field}__in": parent_ids})
        }
        for node in nodes:
            parent = node.parent.obj
            match = by_code.get(node.lgd_code) if node.lgd_code else None
            if match is not None:
                if parent is None or getattr(match, parent_field) != parent.id:
                    self._error(
                        node.lines[0],
                        f"{node.level} '{node.label()}' has LGD code {node.lgd_code}, which belongs to "
                        f"'{match.name_en}' under a different parent in the database. "
                        "Moving areas is not supported by the import.",
                    )
                    continue
            elif parent is not None:
                match = by_name.get((parent.id, name_key(node.name_en)))
                if match is not None and node.lgd_code and match.lgd_code and match.lgd_code != node.lgd_code:
                    self._error(
                        node.lines[0],
                        f"{node.level} '{node.label()}' has LGD code {node.lgd_code} in the file "
                        f"but {match.lgd_code} in the database.",
                    )
                    continue
            node.obj = match

    # --- Writing ------------------------------------------------------------------

    def _write(self) -> None:
        with transaction.atomic():
            for node in self.states.values():
                self._save(node, State, {})
            for node in self.districts.values():
                self._save(node, District, {"state": node.parent.obj})
            for node in self.mandals.values():
                self._save(node, Mandal, {"district": node.parent.obj})
            for node in self.villages.values():
                self._save(
                    node,
                    Village,
                    {"mandal": node.parent.obj},
                    extra={
                        "latitude": node.latitude,
                        "longitude": node.longitude,
                        "pin_code": node.pin_code,
                    },
                )
            self._collect_missing()
            # A conflict found while writing (another admin added the same name) undoes the whole file.
            if self.report.dry_run or not self.report.ok:
                transaction.set_rollback(True)

    def _save(self, node: Node, model, parent: dict, extra: dict | None = None) -> None:
        counts = self.report.counts[node.level]
        values = {"name_en": node.name_en}
        if node.name_te:
            values["name_te"] = node.name_te
        if node.lgd_code:
            values["lgd_code"] = node.lgd_code
        for name, value in (extra or {}).items():
            if value not in (None, ""):
                values[name] = value

        obj = node.obj
        if model is State:
            obj = self._save_state(node, obj, values, counts)
        elif obj is None:
            ((_parent_field, parent_obj),) = parent.items()
            if parent_obj is None:
                return  # its parent failed: that error already fails the file
            level = node.level.upper()
            outcome = self._call_writer(
                node, services.create_area, level, {services.LEVELS[level][3]: parent_obj.id, **values}
            )
            if outcome is None:
                return
            obj = node.obj = outcome.area
            counts.created += 1
        else:
            changed = {name: value for name, value in values.items() if getattr(obj, name) != value}
            if changed:
                outcome = self._call_writer(node, services.update_area, node.level.upper(), changed, area_id=obj.id)
                if outcome is None:
                    return
                obj = node.obj = outcome.area
                counts.updated += 1
            else:
                counts.unchanged += 1
        # save() copies the English name when there is no Telugu one.
        if obj.name_te == obj.name_en:
            self.report.missing_telugu += 1

    def _save_state(self, node: Node, obj, values: dict, counts: Counts):
        """States are rare and outside services.py (only districts and below are edited in the app)."""
        if obj is None:
            obj = node.obj = State.objects.create(**values)
            counts.created += 1
        elif any(getattr(obj, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(obj, name, value)
            obj.save()
            counts.updated += 1
        else:
            counts.unchanged += 1
        return obj

    def _call_writer(self, node: Node, writer, level: str, values: dict, area_id=None):
        """Call the writer for one row; its warnings join the report and its errors fail the file."""
        try:
            if area_id is None:
                outcome = writer(self.actor, level, values, via=self.via, audit=False)
            else:
                outcome = writer(self.actor, level, area_id, values, via=self.via, audit=False)
        except ApiError as error:
            self._error(node.lines[0], f"{node.level} '{node.label()}': {error.detail}")
            return None
        for warning in outcome.warnings:
            if warning["code"] != "name_te_missing":  # missing_telugu counts these
                self._warn(node.lines[0], f"{node.level} '{node.label()}': {warning['message']}")
        return outcome

    def _collect_missing(self) -> None:
        for node in self.villages.values():
            village = node.obj
            if village is not None and not village.has_coordinates:
                mandal = node.parent
                district = mandal.parent
                self.report.missing_coordinates.append(
                    (district.parent.name_en, district.name_en, mandal.name_en, node.name_en, node.lgd_code)
                )

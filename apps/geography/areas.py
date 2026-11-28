"""Area references and containment.

An area is addressed everywhere by a pair: level plus id, for example
("MANDAL", 12). Role assignments and post targets store that pair instead of
a foreign key, so these helpers answer "is this area inside that one?" both
in Python and as an ORM filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db.models import Q

from .models import MODEL_BY_LEVEL, AreaLevel, District, Mandal, State, Village, wants_telugu

LEVELS: tuple[str, ...] = (
    AreaLevel.STATE,
    AreaLevel.DISTRICT,
    AreaLevel.MANDAL,
    AreaLevel.VILLAGE,
)
DEPTH = {level: index for index, level in enumerate(LEVELS)}
PATH_SEPARATOR = " · "


@dataclass(frozen=True, order=True)
class AreaRef:
    level: str
    id: int

    def __post_init__(self):
        if self.level not in DEPTH:
            raise ValueError(f"Unknown area level: {self.level!r}")
        object.__setattr__(self, "id", int(self.id))


def ancestry(area: AreaRef) -> dict[str, int]:
    """Ids of the area and every level above it, e.g. {"STATE": 1, "DISTRICT": 3}.

    Empty when the area does not exist.
    """
    if area.level == AreaLevel.VILLAGE:
        row = (
            Village.objects.filter(id=area.id)
            .values("id", "mandal_id", "mandal__district_id", "mandal__district__state_id")
            .first()
        )
        if row:
            return {
                AreaLevel.VILLAGE: row["id"],
                AreaLevel.MANDAL: row["mandal_id"],
                AreaLevel.DISTRICT: row["mandal__district_id"],
                AreaLevel.STATE: row["mandal__district__state_id"],
            }
    elif area.level == AreaLevel.MANDAL:
        row = Mandal.objects.filter(id=area.id).values("id", "district_id", "district__state_id").first()
        if row:
            return {
                AreaLevel.MANDAL: row["id"],
                AreaLevel.DISTRICT: row["district_id"],
                AreaLevel.STATE: row["district__state_id"],
            }
    elif area.level == AreaLevel.DISTRICT:
        row = District.objects.filter(id=area.id).values("id", "state_id").first()
        if row:
            return {AreaLevel.DISTRICT: row["id"], AreaLevel.STATE: row["state_id"]}
    elif State.objects.filter(id=area.id).exists():
        return {AreaLevel.STATE: area.id}
    return {}


def ancestries(refs: Iterable[AreaRef]) -> dict[AreaRef, dict[str, int]]:
    """ancestry() for many areas: one query per level present. Missing areas map to {}."""
    by_level: dict[str, set[int]] = {}
    for ref in refs:
        by_level.setdefault(ref.level, set()).add(ref.id)
    found: dict[AreaRef, dict[str, int]] = {}
    if by_level.get(AreaLevel.VILLAGE):
        for row in Village.objects.filter(id__in=by_level[AreaLevel.VILLAGE]).values(
            "id", "mandal_id", "mandal__district_id", "mandal__district__state_id"
        ):
            found[AreaRef(AreaLevel.VILLAGE, row["id"])] = {
                AreaLevel.VILLAGE: row["id"],
                AreaLevel.MANDAL: row["mandal_id"],
                AreaLevel.DISTRICT: row["mandal__district_id"],
                AreaLevel.STATE: row["mandal__district__state_id"],
            }
    if by_level.get(AreaLevel.MANDAL):
        for row in Mandal.objects.filter(id__in=by_level[AreaLevel.MANDAL]).values("id", "district_id", "district__state_id"):
            found[AreaRef(AreaLevel.MANDAL, row["id"])] = {
                AreaLevel.MANDAL: row["id"],
                AreaLevel.DISTRICT: row["district_id"],
                AreaLevel.STATE: row["district__state_id"],
            }
    if by_level.get(AreaLevel.DISTRICT):
        for row in District.objects.filter(id__in=by_level[AreaLevel.DISTRICT]).values("id", "state_id"):
            found[AreaRef(AreaLevel.DISTRICT, row["id"])] = {AreaLevel.DISTRICT: row["id"], AreaLevel.STATE: row["state_id"]}
    if by_level.get(AreaLevel.STATE):
        for state_id in State.objects.filter(id__in=by_level[AreaLevel.STATE]).values_list("id", flat=True):
            found[AreaRef(AreaLevel.STATE, state_id)] = {AreaLevel.STATE: state_id}
    return {AreaRef(level, area_id): found.get(AreaRef(level, area_id), {}) for level, ids in by_level.items() for area_id in ids}


def area_is_within(area: AreaRef, container: AreaRef) -> bool:
    """True when `area` is `container` itself or lies below it."""
    if DEPTH[container.level] > DEPTH[area.level]:
        return False
    return ancestry(area).get(container.level) == container.id


def area_exists(area: AreaRef, *, active_only: bool = False) -> bool:
    model = MODEL_BY_LEVEL[area.level]
    queryset = model.objects.active() if active_only else model.objects.all()
    return queryset.filter(id=area.id).exists()


def within_q(container: AreaRef, level_field: str = "area_level", id_field: str = "area_id") -> Q:
    """ORM filter: rows whose (level, id) pair lies inside `container`.

    Works on any model that stores an area as two columns, such as
    RoleAssignment(area_level, area_id) or a post's target.
    """

    def at(level: str, ids) -> Q:
        key = f"{id_field}__in" if not isinstance(ids, int) else id_field
        return Q(**{level_field: level, key: ids})

    cid = container.id
    if container.level == AreaLevel.VILLAGE:
        return at(AreaLevel.VILLAGE, cid)
    if container.level == AreaLevel.MANDAL:
        return at(AreaLevel.MANDAL, cid) | at(
            AreaLevel.VILLAGE, Village.objects.filter(mandal_id=cid).values("id")
        )
    if container.level == AreaLevel.DISTRICT:
        return (
            at(AreaLevel.DISTRICT, cid)
            | at(AreaLevel.MANDAL, Mandal.objects.filter(district_id=cid).values("id"))
            | at(AreaLevel.VILLAGE, Village.objects.filter(mandal__district_id=cid).values("id"))
        )
    return (
        at(AreaLevel.STATE, cid)
        | at(AreaLevel.DISTRICT, District.objects.filter(state_id=cid).values("id"))
        | at(AreaLevel.MANDAL, Mandal.objects.filter(district__state_id=cid).values("id"))
        | at(AreaLevel.VILLAGE, Village.objects.filter(mandal__district__state_id=cid).values("id"))
    )


class AreaDescriber:
    """Names and paths for many areas with one query per level.

    describe() returns {"level", "id", "name", "path"}, where path runs from
    the area up to its district, e.g. "కొలకలూరు · తెనాలి · గుంటూరు".
    """

    def __init__(self, language: str | None = None):
        self.telugu = wants_telugu(language)
        self._cache: dict[AreaRef, dict | None] = {}

    def _name(self, obj) -> str:
        return obj.display_name("te" if self.telugu else "en")

    def prime(self, refs: Iterable[AreaRef]) -> None:
        missing: dict[str, set[int]] = {}
        for ref in refs:
            if ref not in self._cache:
                missing.setdefault(ref.level, set()).add(ref.id)
        loaders = {
            AreaLevel.STATE: lambda ids: State.objects.filter(id__in=ids),
            AreaLevel.DISTRICT: lambda ids: District.objects.filter(id__in=ids),
            AreaLevel.MANDAL: lambda ids: Mandal.objects.filter(id__in=ids).select_related("district"),
            AreaLevel.VILLAGE: lambda ids: Village.objects.filter(id__in=ids).select_related(
                "mandal__district"
            ),
        }
        for level, ids in missing.items():
            for ref_id in ids:
                self._cache[AreaRef(level, ref_id)] = None
            for obj in loaders[level](ids):
                if level == AreaLevel.VILLAGE:
                    chain = [obj, obj.mandal, obj.mandal.district]
                elif level == AreaLevel.MANDAL:
                    chain = [obj, obj.district]
                else:
                    chain = [obj]
                self._cache[AreaRef(level, obj.id)] = {
                    "level": level,
                    "id": obj.id,
                    "name": self._name(obj),
                    "path": PATH_SEPARATOR.join(self._name(item) for item in chain),
                }

    def describe(self, ref: AreaRef) -> dict | None:
        if ref not in self._cache:
            self.prime([ref])
        return self._cache[ref]

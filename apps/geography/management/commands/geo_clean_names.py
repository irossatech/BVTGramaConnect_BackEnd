"""Take the census tags out of village names. Shows what it would do; writes only with --apply.

    python manage.py geo_clean_names            # the list of changes, and what needs a person
    python manage.py geo_clean_names --apply    # rename, through services.update_area (audited)

    (Ct) census town, (Og) out-growth, (Part)   removed: "Anantapur (Ct)" -> "Anantapur"
    (R), (Rural)                                one plain form: "Nellore (R)" -> "Nellore Rural"
    (Near), (East), (West)                      plain words: "Kothapalli (East)" -> "Kothapalli East"

The census spelling goes into census_name, which the CSV import matches on, so a later census
file neither duplicates the village nor puts the tag back. A village whose clean name another
village of its mandal already has (or will have) is left alone and listed: a person names them
apart, for example by hamlet. A missing Telugu name (the English one copied) is cleaned too.
Safe to run again: renamed villages have a census_name and are skipped.
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.geography import services
from apps.geography.models import Village, clean_name, name_key

TAG = re.compile(r"\(\s*(ct|og|part|r|rural|near|east|west)\s*\)", re.IGNORECASE)
REPLACEMENT = {"ct": "", "og": "", "part": "", "r": " Rural", "rural": " Rural",
               "near": " Near", "east": " East", "west": " West"}
REPEATED = re.compile(r"\b(Rural|Near|East|West)(?: \1\b)+", re.IGNORECASE)


def cleaned(name: str) -> str:
    """"Nellore Rural (R)" -> "Nellore Rural"; "" when nothing would be left."""
    out = clean_name(TAG.sub(lambda match: REPLACEMENT[match.group(1).lower()], name))
    return REPEATED.sub(r"\1", out)


class Command(BaseCommand):
    help = "Remove census tags such as (Ct) and (Part) from village names. Writes only with --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Rename the villages (otherwise only list).")

    def handle(self, *args, apply=False, **options):
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        tagged = [v for v in Village.objects.filter(census_name="", name_en__contains="(").select_related(
            "mandal__district") if TAG.search(v.name_en)]
        taken = defaultdict(set)  # mandal -> name keys in use, after the renames planned so far
        for pk, mandal_id, key in Village.objects.filter(mandal_id__in={v.mandal_id for v in tagged}).values_list(
                "id", "mandal_id", "name_key"):
            taken[mandal_id].add(key)
        wanted = defaultdict(list)
        for village in tagged:
            wanted[(village.mandal_id, name_key(cleaned(village.name_en)))].append(village)

        renames, by_hand = [], []
        for village in tagged:
            new = cleaned(village.name_en)
            key = (village.mandal_id, name_key(new))
            if not new or len(wanted[key]) > 1 or key[1] in taken[village.mandal_id]:
                by_hand.append((village, new))
            else:
                renames.append((village, new))
                taken[village.mandal_id].add(key[1])

        for village, new in renames:
            self.stdout.write(f"  {self._where(village)}: {village.name_en} -> {new}")
        if by_hand:
            self.stdout.write(self.style.WARNING(
                f"{len(by_hand)} village(s) need a person: the clean name is taken in the mandal."))
            for village, new in by_hand:
                self.stdout.write(f"  {self._where(village)}: {village.name_en} (id {village.id}) -> {new or '?'}")

        if not apply:
            self.stdout.write(f"{len(renames)} village(s) would be renamed. Run again with --apply to do it.")
            return
        with transaction.atomic():
            for village, new in renames:
                changes = {"name_en": new, "census_name": village.name_en}
                if village.name_te == village.name_en:  # no Telugu name: the copy of the English one
                    changes["name_te"] = new
                services.update_area(None, "VILLAGE", village.id, changes, via="system")
        self.stdout.write(self.style.SUCCESS(f"Renamed {len(renames)} village(s)."))

    @staticmethod
    def _where(village) -> str:
        return f"{village.mandal.district.name_en} > {village.mandal.name_en}"

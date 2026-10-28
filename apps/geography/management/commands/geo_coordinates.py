"""Active villages without coordinates: "Use my location" (geo/nearest/) can never find them. Read-only.

    python manage.py geo_coordinates                     # the count, per district
    python manage.py geo_coordinates --csv missing.csv   # every one, ready to fill in and import
    python manage.py geo_coordinates --fail-above 0      # before a release: fail if any is left

The CSV has the import's columns with latitude and longitude empty. Fill them
in (6 decimals) and import the same file (console or import_geography, a dry
run first): villages match on village_lgd_code, else on their names.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.geography.models import Village

COLUMNS = ["state", "district", "mandal", "village", "village_lgd_code", "pin_code", "latitude", "longitude"]


class Command(BaseCommand):
    help = "Count (and optionally list as CSV) the active villages that have no coordinates. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_path", metavar="PATH", help="Write every such village to this CSV file.")
        parser.add_argument(
            "--fail-above", type=int, metavar="N",
            help="Exit with an error when more than N villages have no coordinates (0 before a release).",
        )

    def handle(self, *args, csv_path=None, fail_above=None, **options):
        # Telugu names must not crash a console that is not UTF-8.
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        active = Village.objects.active()
        missing = active.without_coordinates()
        total, count = active.count(), missing.count()
        self.stdout.write(f"{count} of {total} active village(s) have no coordinates.")
        by_district = (
            missing.order_by()  # no default ordering in the GROUP BY
            .values("mandal__district__name_en")
            .annotate(n=Count("id"))
            .order_by("-n", "mandal__district__name_en")
        )
        for row in by_district:
            self.stdout.write(f"  {row['mandal__district__name_en']:<30} {row['n']:>6}")

        if csv_path:
            self._write_csv(Path(csv_path), missing)
        if fail_above is not None and count > fail_above:
            raise CommandError(f"{count} active village(s) have no coordinates; at most {fail_above} allowed.")
        if count == 0:
            self.stdout.write(self.style.SUCCESS("Every active village has coordinates."))

    def _write_csv(self, target: Path, missing) -> None:
        rows = (
            missing.select_related("mandal__district__state")
            .order_by("mandal__district__name_en", "mandal__name_en", "name_en", "id")
            .iterator(chunk_size=2000)
        )
        written = 0
        # A byte order mark, so Excel shows Telugu and the import reads it as UTF-8.
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for village in rows:
                mandal = village.mandal
                district = mandal.district
                writer.writerow([
                    district.state.name_en, district.name_en, mandal.name_en, village.name_en,
                    village.lgd_code or "", village.pin_code,
                    "" if village.latitude is None else village.latitude,
                    "" if village.longitude is None else village.longitude,
                ])
                written += 1
        self.stdout.write(f"Wrote {written} village(s) to {target}")

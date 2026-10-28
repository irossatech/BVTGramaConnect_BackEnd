import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.geography.importer import LEVELS, OPTIONAL, REQUIRED, GeographyImporter

MAX_LISTED = 20


class Command(BaseCommand):
    help = (
        "Import states, districts, mandals and villages from a UTF-8 CSV file, one row per village. "
        f"Required columns: {', '.join(REQUIRED)}. Optional: {', '.join(OPTIONAL)}. "
        "Validates everything first and writes nothing if there is an error. Safe to run again."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Path to the CSV file.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report what would change, without saving.",
        )
        parser.add_argument(
            "--missing-report",
            metavar="PATH",
            help="Write every village that has no coordinates to this CSV file.",
        )

    def handle(self, *args, csv_path, dry_run, missing_report, **options):
        # Telugu names in messages must not crash a console that is not UTF-8.
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        path = Path(csv_path)
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        report = GeographyImporter(dry_run=dry_run).run_file(path)

        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"Warning: {warning}"))
        if not report.ok:
            for error in report.errors:
                self.stderr.write(self.style.ERROR(error))
            raise CommandError(
                f"{len(report.errors)} error(s) in {path.name}. Nothing was imported."
            )

        heading = "Dry run, nothing saved" if dry_run else "Imported"
        self.stdout.write(self.style.SUCCESS(f"{heading}: {report.rows} row(s) from {path.name}"))
        for level in LEVELS:
            c = report.counts[level]
            self.stdout.write(
                f"  {level + 's':<10} created {c.created:>6}   updated {c.updated:>6}   unchanged {c.unchanged:>6}"
            )
        if report.missing_telugu:
            self.stdout.write(
                self.style.WARNING(
                    f"{report.missing_telugu} area(s) have no Telugu name; the English name is shown instead."
                )
            )

        missing = report.missing_coordinates
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(missing)} village(s) have no coordinates and cannot be found by GPS:"
                )
            )
            for state, district, mandal, village, code in missing[:MAX_LISTED]:
                suffix = f" (LGD {code})" if code else ""
                self.stdout.write(f"  {district} > {mandal} > {village}{suffix}")
            if len(missing) > MAX_LISTED:
                self.stdout.write(f"  … and {len(missing) - MAX_LISTED} more. Use --missing-report for all.")
        else:
            self.stdout.write("Every village in the file has coordinates.")
        if report.missing_coordinates_total:
            self.stdout.write(self.style.WARNING(
                f"{report.missing_coordinates_total} active village(s) in the database have no coordinates. "
                "List them with: manage.py geo_coordinates --csv PATH"
            ))

        if missing_report:
            target = Path(missing_report)
            with open(target, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["state", "district", "mandal", "village", "village_lgd_code"])
                writer.writerows(missing)
            self.stdout.write(f"Wrote {len(missing)} village(s) without coordinates to {target}")

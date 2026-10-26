"""Import important numbers from a CSV file: the same rules as POST /api/v1/admin/contacts/import/.

    python manage.py import_contacts numbers.csv --dry-run
    python manage.py import_contacts numbers.csv

Columns: type, custom_type, name, name_te, phone, email, district_lgd,
mandal_lgd, village_lgd (or district, mandal, village names), sort_order,
consent. All rows or none.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.contacts.services import import_contacts


class Command(BaseCommand):
    help = "Import important numbers from a UTF-8 CSV file. All rows or none."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--dry-run", action="store_true", help="Check every row and save nothing.")

    def handle(self, *args, file, dry_run, **options):
        try:
            text = Path(file).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise CommandError("Save the file as UTF-8 CSV.") from None
        report = import_contacts(None, text, dry_run=dry_run, via="system")
        for error in report.errors:
            self.stderr.write(f"row {error['row']} {error['field']}: {error['message']}")
        if not report.ok:
            raise CommandError("Nothing was imported.")
        verb = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {report.created}, updated {report.updated}, unchanged {report.skipped}."
        ))

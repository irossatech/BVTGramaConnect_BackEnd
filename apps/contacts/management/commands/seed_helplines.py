"""Load the statewide government helplines from a vetted CSV. Safe to run again.

    python manage.py seed_helplines                  # apps/contacts/sample_data/helplines.csv
    python manage.py seed_helplines --file list.csv

Rows match on type, phone and state; a match is updated, not duplicated. The
default file holds only the six numbers already in SITE_HELPLINES until the
owner signs off a longer list (decision C3).
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.contacts.services import import_contacts

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "sample_data" / "helplines.csv"


class Command(BaseCommand):
    help = "Load the statewide government helplines from a CSV file (type,name,name_te,phone,sort_order)."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE))

    def handle(self, *args, file, **options):
        report = import_contacts(None, Path(file).read_text(encoding="utf-8-sig"), via="system")
        if not report.ok:
            raise CommandError("\n".join(f"row {e['row']} {e['field']}: {e['message']}" for e in report.errors))
        self.stdout.write(self.style.SUCCESS(
            f"Helplines: {report.created} created, {report.updated} updated, {report.skipped} unchanged."
        ))

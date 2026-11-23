"""Update and build the Telugu message catalogue (grama/i18n.py), or check it.

    python manage.py translations          add new messages, drop removed ones, build django.mo
    python manage.py translations --check  exit 1 when a message has no Telugu or django.mo is stale

After adding or changing a message in _(): run it, write the Telugu for the new entries in
locale/te/LC_MESSAGES/django.po (empty msgstr ""), run it again, and commit both files.
"""

from django.core.management.base import BaseCommand, CommandError

from grama import i18n


class Command(BaseCommand):
    help = "Update locale/te/LC_MESSAGES/django.po from the code and build django.mo (--check: only check)."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Change nothing; fail when anything is missing.")

    def handle(self, *args, check=False, **options):
        extracted = i18n.extract()
        if not check:
            if extracted.problems:
                raise CommandError("\n".join(extracted.problems))
            added, removed = i18n.update(extracted)
            i18n.compile_mo()
            self.stdout.write(f"{len(extracted.messages)} messages; {len(added)} added, {len(removed)} no longer used.")
            for msgid in added:
                self.stdout.write(f"  new, needs Telugu: {msgid}")
        problems = i18n.check(extracted)
        if problems:
            raise CommandError(f"{len(problems)} problem(s):\n" + "\n".join(problems))
        self.stdout.write(self.style.SUCCESS(f"Telugu catalogue complete: {len(extracted.messages)} messages."))

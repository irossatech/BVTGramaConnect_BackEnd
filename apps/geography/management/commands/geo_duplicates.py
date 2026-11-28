"""List sibling areas with the same name, in English or in Telugu. Read-only.

    python manage.py geo_duplicates

English duplicates block migration 0003 (unique names): run it on every server before
migrating. Telugu duplicates (two "మంత్రిపాలెం" in one mandal) show twice in the app's
lists; new ones are refused, existing ones must be fixed by hand. Fix what it finds with
the owner: rename one area ("Kaza (East)"), or switch it off and rename it.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, F

from apps.geography.models import District, Mandal, Village


class Command(BaseCommand):
    help = "List sibling areas with the same English or Telugu name. Read-only."

    def handle(self, *args, **options):
        # Telugu names must not crash a console that is not UTF-8.
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        found = 0
        for model, parent in ((District, "state"), (Mandal, "district"), (Village, "mandal")):
            for field, label in (("name_key", "English"), ("name_te", "Telugu")):
                rows_in = model.objects.order_by()  # no default ordering in the GROUP BY
                if field == "name_te":
                    # A missing Telugu name holds the English one, which is unique already.
                    rows_in = rows_in.exclude(name_te=F("name_en"))
                groups = rows_in.values(f"{parent}_id", field).annotate(n=Count("id")).filter(n__gt=1)
                for group in groups:
                    found += 1
                    rows = list(
                        model.objects.filter(**{f"{parent}_id": group[f"{parent}_id"], field: group[field]})
                        .order_by("id")
                        .values_list("id", "name_en", "name_te", "is_active")
                    )
                    self.stdout.write(f"{model.__name__} ({label}) under {parent} {group[f'{parent}_id']}: {rows}")
        if found:
            self.stdout.write(self.style.ERROR(f"{found} duplicate group(s). Fix them with the owner."))
        else:
            self.stdout.write(self.style.SUCCESS("No duplicates."))

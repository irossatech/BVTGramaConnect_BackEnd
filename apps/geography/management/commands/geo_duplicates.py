"""List sibling areas with the same name: they block migration 0003 (unique names). Read-only.

    python manage.py geo_duplicates

Run it on every server before migrating. Fix what it finds with the owner:
rename one area ("Kaza (East)"), or switch it off and rename it. Then migrate.
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.geography.models import District, Mandal, Village


class Command(BaseCommand):
    help = "List sibling areas with the same name (blockers for the unique-name migration). Read-only."

    def handle(self, *args, **options):
        found = 0
        for model, parent in ((District, "state"), (Mandal, "district"), (Village, "mandal")):
            groups = (
                model.objects.order_by()  # no default ordering in the GROUP BY
                .values(f"{parent}_id", "name_key")
                .annotate(n=Count("id"))
                .filter(n__gt=1)
            )
            for group in groups:
                found += 1
                rows = list(
                    model.objects.filter(**{f"{parent}_id": group[f"{parent}_id"], "name_key": group["name_key"]})
                    .order_by("id")
                    .values_list("id", "name_en", "is_active")
                )
                self.stdout.write(f"{model.__name__} under {parent} {group[f'{parent}_id']}: {rows}")
        if found:
            self.stdout.write(self.style.ERROR(f"{found} duplicate group(s). Fix them before migrating."))
        else:
            self.stdout.write(self.style.SUCCESS("No duplicates."))

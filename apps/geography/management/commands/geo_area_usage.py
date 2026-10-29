"""What still points at an area, before switching it off or merging it away. Read-only.

    python manage.py geo_area_usage DISTRICT 6

Counts the areas below it, and everything stored with an area pair (level, id) at it or below:
role assignments, post areas and posts sent from there, important numbers, Home banner ads,
service requests and citizen phones. All zero: switching it off hides nothing anyone uses.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.geography.areas import AreaRef, area_exists, within_q
from apps.geography.models import AreaLevel, Mandal, Village


class Command(BaseCommand):
    help = "Count what still refers to an area (and the areas below it). Read-only."

    def add_arguments(self, parser):
        parser.add_argument("level", choices=AreaLevel.values)
        parser.add_argument("area_id", type=int)

    def handle(self, *args, level, area_id, **options):
        from apps.accounts.models import RoleAssignment
        from apps.ads.models import Ad
        from apps.citizens.models import CitizenDevice
        from apps.contacts.models import Contact
        from apps.posts.models import Post, PostTarget
        from apps.service_requests.models import ServiceRequest

        area = AreaRef(level, area_id)
        if not area_exists(area):
            raise CommandError(f"No {level} with id {area_id}.")
        inside = within_q(area)
        village_ids = _villages_in(area)  # a subquery: a state has 18,000 villages
        mandals = {
            AreaLevel.STATE: Mandal.objects.filter(district__state_id=area_id),
            AreaLevel.DISTRICT: Mandal.objects.filter(district_id=area_id),
        }.get(level, Mandal.objects.none())
        counts = {
            "mandals below": mandals.count(),
            "villages below": village_ids.count() if level != AreaLevel.VILLAGE else 0,
            "role assignments": RoleAssignment.objects.filter(inside).count(),
            "post areas": PostTarget.objects.filter(inside).count(),
            "posts sent from it": Post.objects.filter(within_q(area, "posted_as_level", "posted_as_area_id")).count(),
            "important numbers": Contact.objects.filter(inside).count(),
            "banner ads": Ad.objects.filter(inside).count(),
            "service requests": ServiceRequest.objects.filter(village_id__in=village_ids).count(),
            "citizen phones": CitizenDevice.objects.filter(village_id__in=village_ids).count(),
        }
        for name, count in counts.items():
            self.stdout.write(f"  {name:<20} {count:>7}")
        if any(counts.values()):
            self.stdout.write(self.style.WARNING("Still in use: move these first, or keep the area."))
        else:
            self.stdout.write(self.style.SUCCESS("Nothing uses it: it can be switched off."))


def _villages_in(area: AreaRef):
    if area.level == AreaLevel.VILLAGE:
        return Village.objects.filter(id=area.id).values_list("id", flat=True)
    if area.level == AreaLevel.MANDAL:
        return Village.objects.filter(mandal_id=area.id).values_list("id", flat=True)
    if area.level == AreaLevel.DISTRICT:
        return Village.objects.filter(mandal__district_id=area.id).values_list("id", flat=True)
    return Village.objects.filter(mandal__district__state_id=area.id).values_list("id", flat=True)

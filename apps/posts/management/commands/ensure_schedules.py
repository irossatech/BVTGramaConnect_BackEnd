from django.core.management.base import BaseCommand

from apps.posts.tasks import ESCALATION_SCHEDULE, ensure_escalation_schedule
from grama.housekeeping import HOUSEKEEPING_SCHEDULE, ensure_housekeeping_schedule


class Command(BaseCommand):
    help = "Create or update the django-q2 schedules (review escalation, daily housekeeping). Safe to run on every deploy."

    def handle(self, *args, **options):
        ensure_escalation_schedule()
        ensure_housekeeping_schedule()
        self.stdout.write(self.style.SUCCESS(f"Schedules ready: {ESCALATION_SCHEDULE}, {HOUSEKEEPING_SCHEDULE}"))

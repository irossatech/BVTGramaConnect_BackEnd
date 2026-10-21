from django.core.management.base import BaseCommand

from apps.posts.tasks import ESCALATION_SCHEDULE, ensure_escalation_schedule


class Command(BaseCommand):
    help = "Create or update the django-q2 schedules (review escalation). Safe to run on every deploy."

    def handle(self, *args, **options):
        ensure_escalation_schedule()
        self.stdout.write(self.style.SUCCESS(f"Schedule ready: {ESCALATION_SCHEDULE}"))

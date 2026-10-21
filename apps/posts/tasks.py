"""Background jobs for posts, run by django-q2."""

from django.conf import settings

from .services import escalate_overdue

ESCALATION_SCHEDULE = "escalate-overdue-posts"


def run_escalation() -> int:
    """Scheduled every ESCALATION_CHECK_MINUTES by `manage.py ensure_schedules`."""
    return escalate_overdue()


def ensure_escalation_schedule() -> None:
    from django_q.models import Schedule

    Schedule.objects.update_or_create(
        name=ESCALATION_SCHEDULE,
        defaults={
            "func": "apps.posts.tasks.run_escalation",
            "schedule_type": Schedule.MINUTES,
            "minutes": settings.ESCALATION_CHECK_MINUTES,
            "repeats": -1,
        },
    )

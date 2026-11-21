"""Background jobs for citizen phones (django-q2)."""

from django.conf import settings

#: Marks blocks whose time is up as ended and audits citizen.block_ended (owner's request of 2026-11-21).
BLOCK_ENDINGS_SCHEDULE = "end-finished-citizen-blocks"


def run_block_endings() -> int:
    """Scheduled every ESCALATION_CHECK_MINUTES by `manage.py ensure_schedules`."""
    from .services import end_finished_blocks

    return end_finished_blocks()


def ensure_block_endings_schedule() -> None:
    from django_q.models import Schedule

    from grama.schedules import ensure_schedule

    ensure_schedule(
        BLOCK_ENDINGS_SCHEDULE,
        func="apps.citizens.tasks.run_block_endings",
        schedule_type=Schedule.MINUTES,
        minutes=settings.ESCALATION_CHECK_MINUTES,
        repeats=-1,
    )

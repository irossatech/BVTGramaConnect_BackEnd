"""django-q2 schedules by name, kept to one row each (audit finding BE-DB-022).

django-q2's Schedule.name is not unique, so two `ensure_schedules` runs at the same moment (two deploys)
could each create the schedule, and the job would then run twice; update_or_create() would also fail on
every later deploy with MultipleObjectsReturned. ensure_schedule() updates the oldest row of that name,
removes any other, and creates one when there is none.
"""

from __future__ import annotations


def ensure_schedule(name: str, **values):
    from django.db import transaction
    from django_q.models import Schedule

    with transaction.atomic():
        rows = list(Schedule.objects.filter(name=name).order_by("pk"))
        if not rows:
            return Schedule.objects.create(name=name, **values)
        keep, extra = rows[0], rows[1:]
        for field, value in values.items():
            setattr(keep, field, value)
        keep.save()
        if extra:
            Schedule.objects.filter(pk__in=[row.pk for row in extra]).delete()
        return keep

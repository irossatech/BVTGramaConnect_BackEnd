from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views import defaults
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def worker_state(now) -> str:
    """Is the background worker (qcluster) running? Read from the escalation schedule.

    The worker moves the schedule's next_run forward each time it runs the job, every
    ESCALATION_CHECK_MINUTES. A next_run more than one interval in the past means runs were
    missed: the worker is stopped or stuck. "not_scheduled" until `manage.py ensure_schedules` ran.
    """
    from django_q.models import Schedule

    from apps.posts.tasks import ESCALATION_SCHEDULE

    next_run = Schedule.objects.filter(name=ESCALATION_SCHEDULE).values_list("next_run", flat=True).first()
    if next_run is None:
        return "not_scheduled"
    return "late" if now - next_run > timedelta(minutes=settings.ESCALATION_CHECK_MINUTES) else "ok"


@extend_schema(
    summary="Health check",
    responses=inline_serializer(
        name="Health",
        fields={
            "status": serializers.CharField(),
            "time": serializers.DateTimeField(),
            "worker": serializers.ChoiceField(choices=["ok", "late", "not_scheduled"]),
        },
    ),
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    """Answers 200 when Django is up and the database answers.

    `worker` tells whether background jobs (pushes, escalation, translation) are running. It does
    not change the status code: deploys check this address before they start the worker.
    """
    connection.ensure_connection()
    now = timezone.now()
    return Response({"status": "ok", "time": now, "worker": worker_state(now)})


# --- Error pages once DEBUG is off (handler404 / handler500 in grama/urls.py) -----------------
# API clients get the contract's error shape (section 1), never an HTML page. Nothing about the
# server, its URLs or the failed request is included. Other addresses keep Django's plain pages.


def _is_api(request) -> bool:
    return request.path.startswith("/api/")


def not_found(request, exception=None):
    if _is_api(request):
        return JsonResponse({"error": {"code": "not_found", "message": "No such address."}}, status=404)
    return defaults.page_not_found(request, exception)


def server_error(request):
    if _is_api(request):
        return JsonResponse({"error": {"code": "server_error", "message": "Something went wrong on the server."}}, status=500)
    return defaults.server_error(request)

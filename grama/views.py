import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import defaults
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger("grama.health")


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


def failed_jobs(now) -> int:
    """Background jobs that failed in the last 24 hours (finding BE-OBS-204).

    django-q2 keeps each failed job (housekeeping deletes them after HOUSEKEEPING_KEEP_DAYS) and writes
    it to the worker log at ERROR; Django admin is off on servers, so this count is where they show.
    """
    from django_q.models import Failure

    return Failure.objects.filter(stopped__gte=now - timedelta(hours=24)).count()


@extend_schema(
    summary="Health check",
    parameters=[OpenApiParameter("strict", bool, description="1: answer 503 while the worker is not running.")],
    responses={
        200: inline_serializer(
            name="Health",
            fields={
                "status": serializers.ChoiceField(choices=["ok", "worker_down"]),
                "time": serializers.DateTimeField(),
                "worker": serializers.ChoiceField(choices=["ok", "late", "not_scheduled"]),
                "failed_jobs_24h": serializers.IntegerField(),
            },
        ),
        503: inline_serializer(
            name="HealthDown",
            fields={"status": serializers.ChoiceField(choices=["database_down", "worker_down"]), "time": serializers.DateTimeField()},
        ),
    },
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    """For the deploy scripts and an uptime monitor (finding BE-OBS-201).

    200 while Django is up and the database answers; 503 `database_down` when it does not. `worker`
    tells whether background jobs (pushes, escalation, translation) are running; it changes the status
    code only with `?strict=1`, the address for an uptime monitor, because the deploy scripts call
    health/ before they start the worker. `failed_jobs_24h` counts failed jobs (BE-OBS-204).
    """
    now = timezone.now()
    try:
        connection.ensure_connection()
        worker = worker_state(now)
        failed = failed_jobs(now)
    except DatabaseError:
        logger.error("Health check: the database does not answer.")
        return Response({"status": "database_down", "time": now}, status=503)
    if request.query_params.get("strict") == "1" and worker != "ok":
        return Response({"status": "worker_down", "time": now, "worker": worker, "failed_jobs_24h": failed}, status=503)
    return Response({"status": "ok", "time": now, "worker": worker, "failed_jobs_24h": failed})


# --- Error pages once DEBUG is off (handler404 / handler500 in grama/urls.py) -----------------
# API clients get the contract's error shape (section 1), never an HTML page. Nothing about the
# server, its URLs or the failed request is included. Other addresses keep Django's plain pages.


def _is_api(request) -> bool:
    return request.path.startswith("/api/")


def _error_response(error: dict, status: int) -> JsonResponse:
    # Raw UTF-8 like every other API answer (UNICODE_JSON): the message may be Telugu.
    return JsonResponse({"error": error}, status=status, json_dumps_params={"ensure_ascii": False})


def not_found(request, exception=None):
    if _is_api(request):
        return _error_response({"code": "not_found", "message": _("No such address.")}, 404)
    return defaults.page_not_found(request, exception)


def server_error(request):
    if _is_api(request):
        # request_id: the number to quote when reporting it; the server log has the details under it.
        error = {"code": "server_error", "message": _("Something went wrong on the server.")}
        request_id = getattr(request, "request_id", None)
        if request_id:
            error["request_id"] = request_id
        return _error_response(error, 500)
    return defaults.server_error(request)

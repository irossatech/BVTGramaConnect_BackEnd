from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views import defaults
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(
    summary="Health check",
    responses=inline_serializer(
        name="Health",
        fields={"status": serializers.CharField(), "time": serializers.DateTimeField()},
    ),
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    """Answers 200 when Django is up and the database answers."""
    connection.ensure_connection()
    return Response({"status": "ok", "time": timezone.now()})


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

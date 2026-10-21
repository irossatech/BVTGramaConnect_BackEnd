from django.db import connection
from django.utils import timezone
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

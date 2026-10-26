"""Shapes used only to describe responses in the OpenAPI schema (drf-spectacular)."""

from rest_framework import serializers


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False)


class ErrorSerializer(serializers.Serializer):
    """Every error: {"error": {"code", "message", "fields"?}} (grama/exceptions.py)."""

    error = ErrorBodySerializer()

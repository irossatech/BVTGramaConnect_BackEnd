"""JSON for every API response, with every time in India Standard Time.

Times are stored in UTC (USE_TZ) and go out in TIME_ZONE (Asia/Kolkata), ISO
8601 with the offset: "2026-10-26T15:00:00+05:30". Serializer fields already
do that; this encoder does the same for times a view puts in a response
directly (the health check, dashboards, reports), so every endpoint agrees.
"""

import datetime

from django.utils import timezone
from rest_framework import renderers
from rest_framework.utils.encoders import JSONEncoder


class LocalTimeJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime) and timezone.is_aware(obj):
            obj = timezone.localtime(obj)
        return super().default(obj)


class JSONRenderer(renderers.JSONRenderer):
    encoder_class = LocalTimeJSONEncoder

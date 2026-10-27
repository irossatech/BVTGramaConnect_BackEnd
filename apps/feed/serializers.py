"""Slim post shapes for citizens on 2G.

The list carries the full body, not an excerpt: the app's news deck shows the
body on the card, and one bigger page costs less than a request per card.
"""

from django.core.files.storage import default_storage
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.geography.areas import AreaDescriber
from apps.posts.models import MediaKind
from apps.posts.serializers import EventSerializer


class AreaSlimSerializer(serializers.Serializer):
    level = serializers.CharField()
    id = serializers.IntegerField()
    name = serializers.CharField()


class FeedVideoSerializer(serializers.Serializer):
    url = serializers.CharField()
    poster_url = serializers.CharField()
    duration_s = serializers.IntegerField(allow_null=True)


class FeedMediaSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()
    poster_url = serializers.SerializerMethodField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    duration_s = serializers.IntegerField(allow_null=True)

    def get_url(self, obj) -> str:
        return obj.video.url if obj.kind == MediaKind.VIDEO else obj.image.url

    def get_thumb_url(self, obj) -> str:
        return obj.thumbnail.url

    def get_poster_url(self, obj) -> str | None:
        return obj.image.url if obj.kind == MediaKind.VIDEO else None


class FeedItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    category = serializers.CharField(source="category.key")
    language = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    area = serializers.SerializerMethodField()
    published_at = serializers.DateTimeField()
    thumbnail_url = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    media_count = serializers.SerializerMethodField()
    source_role = serializers.CharField(source="posted_as_role")
    event = serializers.SerializerMethodField()
    is_urgent = serializers.BooleanField()
    video = serializers.SerializerMethodField()

    def _describer(self) -> AreaDescriber:
        describer = self.context.get("describer")
        if describer is None:
            describer = self.context["describer"] = AreaDescriber()
        return describer

    @extend_schema_field(AreaSlimSerializer)
    def get_area(self, obj):
        described = self._describer().describe(obj.target)
        if described is None:
            return {"level": obj.target_level, "id": obj.target_area_id, "name": ""}
        return {"level": described["level"], "id": described["id"], "name": described["name"]}

    def get_thumbnail_url(self, obj) -> str | None:
        name = getattr(obj, "thumbnail_name", None)
        return default_storage.url(name) if name else None

    def get_image_url(self, obj) -> str | None:
        """First photo at full size (POST_IMAGE_MAX_PX, 1280 px); null without a photo or for a video post."""
        name = getattr(obj, "image_name", None)
        return default_storage.url(name) if name else None

    def get_media_count(self, obj) -> int:
        count = getattr(obj, "media_count", None)
        return count if count is not None else obj.media.count()

    @extend_schema_field(EventSerializer(allow_null=True))
    def get_event(self, obj):
        return obj.event

    @extend_schema_field(FeedVideoSerializer(allow_null=True))
    def get_video(self, obj):
        name = getattr(obj, "video_name", None)
        if not name:
            return None
        return {
            "url": default_storage.url(name),
            "poster_url": default_storage.url(obj.video_poster_name),
            "duration_s": obj.video_duration,
        }


class FeedPostSerializer(FeedItemSerializer):
    path = serializers.SerializerMethodField()
    media = FeedMediaSerializer(many=True)

    def get_path(self, obj) -> str:
        described = self._describer().describe(obj.target)
        return described["path"] if described else ""

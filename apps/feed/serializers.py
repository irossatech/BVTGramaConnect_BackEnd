"""Slim post shapes for citizens on 2G.

The list carries the full body, not an excerpt: the app's news deck shows the
body on the card, and one bigger page costs less than a request per card.
"""

from django.core.files.storage import default_storage
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.geography.areas import AreaDescriber
from apps.posts.models import MediaKind
from apps.posts.serializers import EventSerializer, TranslationSerializer, describe_targets, translation_of
from apps.posts.sharing import share_url


def media_url(context, name: str) -> str:
    """The full link to an uploaded file ("https://<site>/media/..."), as every endpoint sends it
    (contract section 1, BVTGC-68). Only without a request (a test or a script) is it the path."""
    url = default_storage.url(name)
    request = context.get("request")
    return request.build_absolute_uri(url) if request is not None else url


class AreaSlimSerializer(serializers.Serializer):
    level = serializers.CharField()
    id = serializers.IntegerField()
    name = serializers.CharField()


class AreaTargetSerializer(AreaSlimSerializer):
    path = serializers.CharField()


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
        return media_url(self.context, obj.video.name if obj.kind == MediaKind.VIDEO else obj.image.name)

    def get_thumb_url(self, obj) -> str:
        return media_url(self.context, obj.thumbnail.name)

    def get_poster_url(self, obj) -> str | None:
        return media_url(self.context, obj.image.name) if obj.kind == MediaKind.VIDEO else None


class FeedItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    category = serializers.CharField(source="category.key")
    language = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    translation = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()
    targets = serializers.SerializerMethodField()
    published_at = serializers.DateTimeField()
    thumbnail_url = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    medium_url = serializers.SerializerMethodField()
    media_count = serializers.SerializerMethodField()
    source_role = serializers.CharField(source="posted_as_role")
    source_area = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    is_urgent = serializers.BooleanField()
    video = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()

    def get_share_url(self, obj) -> str | None:
        """The post's public link (/p/<code>/), or null while PUBLIC_BASE_URL is not set."""
        return share_url(obj)

    def _describer(self) -> AreaDescriber:
        describer = self.context.get("describer")
        if describer is None:
            describer = self.context["describer"] = AreaDescriber()
        return describer

    @extend_schema_field(TranslationSerializer(allow_null=True))
    def get_translation(self, obj):
        return translation_of(obj)

    @extend_schema_field(AreaTargetSerializer(many=True))
    def get_targets(self, obj):
        return describe_targets(self._describer(), obj)

    @extend_schema_field(AreaSlimSerializer)
    def get_area(self, obj):
        described = self._describer().describe(obj.target)
        if described is None:
            return {"level": obj.target_level, "id": obj.target_area_id, "name": ""}
        return {"level": described["level"], "id": described["id"], "name": described["name"]}

    @extend_schema_field(AreaSlimSerializer(allow_null=True))
    def get_source_area(self, obj):
        """The poster's own area ("Kolakaluru Gram Panchayat", "a citizen of Kolakaluru"), not where it was sent."""
        described = self._describer().describe(obj.posted_as)
        if described is None:
            return None
        return {"level": described["level"], "id": described["id"], "name": described["name"]}

    def get_thumbnail_url(self, obj) -> str | None:
        name = getattr(obj, "thumbnail_name", None)
        return media_url(self.context, name) if name else None

    def get_image_url(self, obj) -> str | None:
        """First photo at full size (POST_IMAGE_MAX_PX, 1280 px); null without a photo or for a video post."""
        name = getattr(obj, "image_name", None)
        return media_url(self.context, name) if name else None

    def get_medium_url(self, obj) -> str | None:
        """First photo at POST_MEDIUM_PX (640 px), for cards on slow networks (BVTGC-95); null without a
        photo, for a video post, and for an older photo until backfill_medium_images made its copy."""
        name = getattr(obj, "medium_name", None)
        return media_url(self.context, name) if name else None

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
            "url": media_url(self.context, name),
            "poster_url": media_url(self.context, obj.video_poster_name),
            "duration_s": obj.video_duration,
        }


class FeedPostSerializer(FeedItemSerializer):
    path = serializers.SerializerMethodField()
    media = FeedMediaSerializer(many=True)

    def get_path(self, obj) -> str:
        described = self._describer().describe(obj.target)
        return described["path"] if described else ""

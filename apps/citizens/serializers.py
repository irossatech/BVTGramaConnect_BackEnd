"""Shapes from docs/api-contract.md, section 15."""

from django.core.files.storage import default_storage
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.feed.serializers import AreaSlimSerializer
from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel
from apps.posts.models import Category, MediaKind, PostStatus, ReviewAction
from apps.posts.serializers import TranslationSerializer, translation_of

from .models import CitizenDevice


def _village(describer: AreaDescriber, village_id: int) -> dict:
    described = describer.describe(AreaRef(AreaLevel.VILLAGE, village_id))
    if described is None:
        return {"level": AreaLevel.VILLAGE, "id": village_id, "name": ""}
    return {"level": described["level"], "id": described["id"], "name": described["name"]}


def _describer(context) -> AreaDescriber:
    if "describer" not in context:
        context["describer"] = AreaDescriber()
    return context["describer"]


class ChallengeSerializer(serializers.Serializer):
    nonce = serializers.CharField()
    expires_in = serializers.IntegerField()


LANGUAGES = [("te", "te"), ("en", "en")]


class RegisterSerializer(serializers.Serializer):
    nonce = serializers.CharField(max_length=200)
    integrity_token = serializers.CharField(max_length=20000, required=False, allow_blank=True, default="")
    platform = serializers.ChoiceField(choices=["android", "ios"])
    village_id = serializers.IntegerField(min_value=1)
    app_version = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    #: Android's Settings.Secure.ANDROID_ID: links reinstalls, so a block survives them. Stored only hashed.
    android_id = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    #: FCM token for "your post was published / discarded" (CP4).
    fcm_token = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    language = serializers.ChoiceField(choices=LANGUAGES, required=False, default="te")


class DeviceUpdateSerializer(serializers.Serializer):
    village_id = serializers.IntegerField(min_value=1, required=False)
    fcm_token = serializers.CharField(max_length=255, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=LANGUAGES, required=False)


class CitizenDeviceSerializer(serializers.ModelSerializer):
    village = serializers.SerializerMethodField()
    blocked = serializers.SerializerMethodField()
    block_reason = serializers.SerializerMethodField()
    blocked_until = serializers.SerializerMethodField()
    posts_today = serializers.SerializerMethodField()
    posts_per_day = serializers.SerializerMethodField()

    class Meta:
        model = CitizenDevice
        fields = [
            "id", "village", "language", "blocked", "block_reason", "blocked_until",
            "posts_today", "posts_per_day", "created_at",
        ]

    def _block(self, obj):
        from .services import active_block

        cache = self.context.setdefault("blocks", {})
        if obj.pk not in cache:
            cache[obj.pk] = active_block(obj)
        return cache[obj.pk]

    @extend_schema_field(AreaSlimSerializer)
    def get_village(self, obj):
        return _village(_describer(self.context), obj.village_id)

    def get_blocked(self, obj) -> bool:
        """Blocked on this install or any earlier install of the same phone."""
        return self._block(obj) is not None

    def get_block_reason(self, obj) -> str | None:
        block = self._block(obj)
        return block.block_reason if block else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_blocked_until(self, obj):
        block = self._block(obj)
        return serializers.DateTimeField().to_representation(block.blocked_until) if block and block.blocked_until else None

    def get_posts_today(self, obj) -> int:
        from .services import posts_today

        return posts_today(obj).count()

    def get_posts_per_day(self, obj) -> int | None:
        """null: no daily limit."""
        from .services import posts_per_day

        return posts_per_day() or None


class RegisteredSerializer(serializers.Serializer):
    token = serializers.CharField()
    device = CitizenDeviceSerializer()


class CitizenPostWriteSerializer(serializers.Serializer):
    category = serializers.SlugRelatedField(slug_field="key", queryset=Category.objects.filter(is_active=True))
    language = serializers.ChoiceField(choices=["te", "en"], default="te")
    title = serializers.CharField(max_length=150)
    body = serializers.CharField(max_length=5000)
    photos = serializers.ListField(child=serializers.FileField(), required=False, default=list)
    videos = serializers.ListField(child=serializers.FileField(), required=False, default=list)

    def validate_title(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field may not be blank.")
        return value


class CitizenPhotoSerializer(serializers.Serializer):
    url = serializers.CharField()
    thumb_url = serializers.CharField()


class CitizenVideoSerializer(serializers.Serializer):
    url = serializers.CharField()
    poster_url = serializers.CharField()
    thumb_url = serializers.CharField()
    duration_s = serializers.IntegerField(allow_null=True)


class CitizenPostSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    category = serializers.CharField(source="category.key")
    language = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    translation = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    videos = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.SerializerMethodField()

    @extend_schema_field(TranslationSerializer(allow_null=True))
    def get_translation(self, obj):
        return translation_of(obj)

    @extend_schema_field(AreaSlimSerializer)
    def get_area(self, obj):
        return _village(_describer(self.context), obj.target_area_id)

    @extend_schema_field(CitizenPhotoSerializer(many=True))
    def get_photos(self, obj):
        return [
            {"url": default_storage.url(item.image.name), "thumb_url": default_storage.url(item.thumbnail.name)}
            for item in obj.media.all()
            if item.kind == MediaKind.IMAGE
        ]

    @extend_schema_field(CitizenVideoSerializer(many=True))
    def get_videos(self, obj):
        return [
            {
                "url": default_storage.url(item.video.name),
                "poster_url": default_storage.url(item.image.name),
                "thumb_url": default_storage.url(item.thumbnail.name),
                "duration_s": item.duration_s,
            }
            for item in obj.media.all()
            if item.kind == MediaKind.VIDEO
        ]

    def get_reason(self, obj) -> str | None:
        """The reviewer's comment when the post was discarded or taken down."""
        if obj.status not in (PostStatus.DISCARDED, PostStatus.UNPUBLISHED):
            return None
        wanted = {PostStatus.DISCARDED: ReviewAction.Action.DISCARD, PostStatus.UNPUBLISHED: ReviewAction.Action.UNPUBLISH}
        actions = [a for a in obj.review_actions.all() if a.to_status == obj.status and a.action in (wanted[obj.status], ReviewAction.Action.REVERSE)]
        latest = max(actions, key=lambda a: a.created_at, default=None)
        return latest.comment if latest and latest.comment else None

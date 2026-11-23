"""Shapes from docs/api-contract.md, section 15."""


from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.feed.serializers import AreaSlimSerializer, AreaTargetSerializer, media_url
from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel
from apps.posts.models import Category, MediaKind, PostStatus, ReviewAction
from apps.posts.serializers import PersonSerializer, TranslationSerializer, describe_targets, translation_of

from .models import CitizenBlock, CitizenDevice


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


@extend_schema_serializer(component_name="CitizenDeviceUpdate")
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


def _official(user, role: str) -> dict | None:
    """{"id", "name", "role"} of the official, or None when no one did it (a block whose time was up)."""
    if user is None and not role:
        return None
    return {"id": str(user.id) if user else None, "name": user.name if user else "", "role": role}


class CitizenBlockSerializer(serializers.ModelSerializer):
    """One block of one phone (owner's request of 2026-11-21). ended_at null: still running, unless its time is up."""

    blocked = serializers.SerializerMethodField()
    blocked_by = serializers.SerializerMethodField()
    ended_by = serializers.SerializerMethodField()

    class Meta:
        model = CitizenBlock
        fields = ["blocked", "blocked_at", "blocked_until", "blocked_by", "reason", "ended_at", "ended_by"]

    def get_blocked(self, obj) -> bool:
        now = self.context.get("now")
        return obj.ended_at is None and (obj.blocked_until is None or (now is not None and obj.blocked_until > now))

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_blocked_by(self, obj):
        return _official(obj.blocked_by, obj.blocked_by_role)

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_ended_by(self, obj):
        """null with ended_at set: the time was up."""
        return _official(obj.ended_by, obj.ended_by_role)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if obj.ended_at is None and obj.blocked_until is not None and not data["blocked"]:
            # The time is up but the job has not marked it yet: it ended at its end date.
            data["ended_at"] = serializers.DateTimeField().to_representation(obj.blocked_until)
        return data


class BlockedCitizenSerializer(CitizenBlockSerializer):
    """A row of GET /admin/citizens/blocked/: the phone's latest block and its post counts. No personal data."""

    id = serializers.SerializerMethodField()
    village = serializers.SerializerMethodField()
    posts_total = serializers.SerializerMethodField()
    posts_discarded = serializers.SerializerMethodField()
    posts_blocked = serializers.SerializerMethodField()
    latest_post_id = serializers.SerializerMethodField()

    class Meta(CitizenBlockSerializer.Meta):
        fields = [
            "id", "village", *CitizenBlockSerializer.Meta.fields,
            "posts_total", "posts_discarded", "posts_blocked", "latest_post_id",
        ]

    def _record(self, obj):
        return self.context.get("records", {}).get(obj.phone)

    @extend_schema_field(serializers.UUIDField())
    def get_id(self, obj):
        """The phone's first install: use it for /admin/citizens/{id}/… and GET /posts/?citizen=."""
        anchor = self.context.get("anchors", {}).get(obj.phone)
        return str(anchor.pk if anchor else obj.device_id)

    @extend_schema_field(AreaSlimSerializer)
    def get_village(self, obj):
        return _village(_describer(self.context), obj.village_id)

    def get_posts_total(self, obj) -> int:
        record = self._record(obj)
        return record.posts_total if record else 0

    def get_posts_discarded(self, obj) -> int:
        record = self._record(obj)
        return record.posts_discarded if record else 0

    def get_posts_blocked(self, obj) -> int:
        record = self._record(obj)
        return record.posts_blocked if record else 0

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_latest_post_id(self, obj):
        post_id = self.context.get("latest_posts", {}).get(obj.phone)
        return str(post_id) if post_id else None


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
    # No `targets`: a citizen's post is for the phone's village only (owner's decision of 2026-11-19).
    # A `targets` field from an older app is ignored, like any unknown field, so its posts still go
    # through, to the phone's village.
    #: The app's random UUID for this post, the same on a repeat after a lost answer (MOB-ERR-004).
    client_ref = serializers.UUIDField(required=False)

    def validate_title(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("This field may not be blank."))
        return value

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("This field may not be blank."))
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
    #: BLOCKED (decision of 2026-11-20) is told to the phone as DISCARDED: the citizen app knows no BLOCKED.
    status = serializers.SerializerMethodField()
    category = serializers.CharField(source="category.key")
    language = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    translation = serializers.SerializerMethodField()
    area = serializers.SerializerMethodField()
    targets = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    videos = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        return PostStatus.DISCARDED if obj.status == PostStatus.BLOCKED else obj.status

    @extend_schema_field(TranslationSerializer(allow_null=True))
    def get_translation(self, obj):
        return translation_of(obj)

    @extend_schema_field(AreaSlimSerializer)
    def get_area(self, obj):
        """The phone's village the post was sent from."""
        return _village(_describer(self.context), obj.posted_as_area_id)

    @extend_schema_field(AreaTargetSerializer(many=True))
    def get_targets(self, obj):
        return describe_targets(_describer(self.context), obj)

    @extend_schema_field(CitizenPhotoSerializer(many=True))
    def get_photos(self, obj):
        return [
            {"url": media_url(self.context, item.image.name), "thumb_url": media_url(self.context, item.thumbnail.name)}
            for item in obj.media.all()
            if item.kind == MediaKind.IMAGE
        ]

    @extend_schema_field(CitizenVideoSerializer(many=True))
    def get_videos(self, obj):
        return [
            {
                "url": media_url(self.context, item.video.name),
                "poster_url": media_url(self.context, item.image.name),
                "thumb_url": media_url(self.context, item.thumbnail.name),
                "duration_s": item.duration_s,
            }
            for item in obj.media.all()
            if item.kind == MediaKind.VIDEO
        ]

    def get_reason(self, obj) -> str | None:
        """The reviewer's comment when the post was discarded or taken down."""
        if obj.status not in (PostStatus.DISCARDED, PostStatus.UNPUBLISHED, PostStatus.BLOCKED):
            return None
        wanted = {
            PostStatus.DISCARDED: ReviewAction.Action.DISCARD,
            PostStatus.UNPUBLISHED: ReviewAction.Action.UNPUBLISH,
            PostStatus.BLOCKED: ReviewAction.Action.BLOCK,
        }
        actions = [a for a in obj.review_actions.all() if a.to_status == obj.status and a.action in (wanted[obj.status], ReviewAction.Action.REVERSE)]
        latest = max(actions, key=lambda a: a.created_at, default=None)
        return latest.comment if latest and latest.comment else None

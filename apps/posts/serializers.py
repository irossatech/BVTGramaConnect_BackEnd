"""Post shapes from docs/api-contract.md, section 2 ("Post", "Media", "ReviewAction")."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.serializers import AreaInputSerializer, AreaOutputSerializer, describer_from

from .models import Category, EventKind, MediaKind, Post, PostMedia, PostStatus, ReviewAction
from .services import REVERSE_TARGETS, allowed_actions

DECISION_ACTIONS = {
    ReviewAction.Action.PUBLISH,
    ReviewAction.Action.AUTO_PUBLISH,
    ReviewAction.Action.RECHECK,
    ReviewAction.Action.DISCARD,
    ReviewAction.Action.UNPUBLISH,
    ReviewAction.Action.REVERSE,
}


class CategorySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    short_name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["key", "name", "short_name", "order"]

    def get_name(self, obj) -> str:
        return obj.display_name()

    def get_short_name(self, obj) -> str:
        return obj.display_name(short=True)


class AdminCategorySerializer(serializers.ModelSerializer):
    post_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = [
            "id", "key", "name_te", "name_en", "short_name_te", "short_name_en",
            "is_active", "order", "post_count",
        ]
        read_only_fields = ["id"]


class PersonSerializer(serializers.Serializer):
    id = serializers.UUIDField(allow_null=True)
    name = serializers.CharField()
    role = serializers.CharField()


def person(user, role: str) -> dict:
    if user is None and not role:
        return {"id": None, "name": "System", "role": "SYSTEM"}
    return {"id": str(user.id) if user else None, "name": user.name if user else "", "role": role}


class MediaSerializer(serializers.ModelSerializer):
    """A photo, or a video with its poster (owner decision Q8)."""

    url = serializers.SerializerMethodField()
    poster_url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = ["id", "kind", "url", "poster_url", "thumb_url", "width", "height", "duration_s", "bytes"]

    def _absolute(self, field) -> str:
        request = self.context.get("request")
        return request.build_absolute_uri(field.url) if request else field.url

    def get_url(self, obj) -> str:
        return self._absolute(obj.video if obj.kind == MediaKind.VIDEO else obj.image)

    def get_poster_url(self, obj) -> str | None:
        return self._absolute(obj.image) if obj.kind == MediaKind.VIDEO else None

    def get_thumb_url(self, obj) -> str:
        return self._absolute(obj.thumbnail)


class ReviewActionSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    at = serializers.DateTimeField(source="created_at")

    class Meta:
        model = ReviewAction
        fields = ["id", "action", "from_status", "to_status", "comment", "actor", "at"]

    @extend_schema_field(PersonSerializer)
    def get_actor(self, obj):
        return person(obj.actor, obj.actor_role)


class LastReviewSerializer(serializers.Serializer):
    action = serializers.CharField()
    comment = serializers.CharField()
    actor = PersonSerializer()
    at = serializers.DateTimeField()


class PostedAsSerializer(serializers.Serializer):
    role = serializers.CharField()
    area = AreaOutputSerializer()


class EventSerializer(serializers.Serializer):
    """Optional event date on a post (owner decision Q7)."""

    kind = serializers.ChoiceField(choices=EventKind.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if attrs.get("end_date") and attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": [_("The end date cannot be before the start date.")]})
        return attrs


class CitizenSummarySerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    blocked = serializers.BooleanField()
    blocked_until = serializers.DateTimeField(allow_null=True)
    posts_total = serializers.IntegerField()
    posts_discarded = serializers.IntegerField()


class CitizenBlockSerializer(serializers.Serializer):
    """The reason is required (contract section 15); it is also the discard reason of the phone's pending posts."""

    comment = serializers.CharField(max_length=500)
    #: A block is temporary; the Super Admin picks its length each time (lead's decision of 2026-10-27).
    days = serializers.IntegerField(min_value=1, max_value=365)


class CitizenDiscardAllSerializer(serializers.Serializer):
    comment = serializers.CharField(max_length=500)


class TranslationSerializer(serializers.Serializer):
    language = serializers.CharField(help_text="Always the language the post is not written in")
    title = serializers.CharField()
    body = serializers.CharField()
    machine = serializers.BooleanField(help_text="false once an official corrected it")


def translation_of(post) -> dict | None:
    """The post in its other language, or None: none yet, the provider is off, or the post changed since.

    Reads post.translations.all(), so lists prefetch "translations".
    """
    for row in post.translations.all():
        if (row.language == post.other_language and row.source_hash == post.source_hash
                and row.title.strip() and row.body.strip()):
            return {"language": row.language, "title": row.title, "body": row.body, "machine": row.provider != "manual"}
    return None


class PostSerializer(serializers.ModelSerializer):
    category = CategorySerializer()
    translation = serializers.SerializerMethodField()
    target_area = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    posted_as = serializers.SerializerMethodField()
    escalated = serializers.SerializerMethodField()
    decided_by = serializers.SerializerMethodField()
    last_review = serializers.SerializerMethodField()
    citizen = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "status", "category", "language", "title", "body", "translation",
            "target_level", "target_area", "event", "is_urgent", "media", "author", "posted_as",
            "created_at", "updated_at", "submitted_at", "published_at",
            "escalated", "escalated_at", "escalation_level",
            "decided_by", "decided_at", "last_review", "citizen", "allowed_actions",
        ]

    @extend_schema_field(TranslationSerializer(allow_null=True))
    def get_translation(self, obj):
        return translation_of(obj)

    @extend_schema_field(EventSerializer(allow_null=True))
    def get_event(self, obj):
        return obj.event

    @extend_schema_field(AreaOutputSerializer)
    def get_target_area(self, obj):
        return describer_from(self.context).describe(obj.target)

    @extend_schema_field(MediaSerializer(many=True))
    def get_media(self, obj):
        return MediaSerializer(obj.media.all(), many=True, context=self.context).data

    @extend_schema_field(PersonSerializer)
    def get_author(self, obj):
        return person(obj.author, obj.posted_as_role)

    @extend_schema_field(PostedAsSerializer)
    def get_posted_as(self, obj):
        return {"role": obj.posted_as_role, "area": describer_from(self.context).describe(obj.posted_as)}

    def get_escalated(self, obj) -> bool:
        return obj.status == PostStatus.PENDING and obj.escalated_at is not None

    @extend_schema_field(PersonSerializer(allow_null=True))
    def get_decided_by(self, obj):
        if obj.decided_by_id is None:
            return None
        return person(obj.decided_by, obj.decided_by_role)

    @extend_schema_field(LastReviewSerializer(allow_null=True))
    def get_last_review(self, obj):
        actions = [a for a in obj.review_actions.all() if a.action in DECISION_ACTIONS]
        if not actions:
            return None
        latest = max(actions, key=lambda a: a.created_at)
        return {
            "action": latest.action,
            "comment": latest.comment,
            "actor": person(latest.actor, latest.actor_role),
            "at": serializers.DateTimeField().to_representation(latest.created_at),
        }

    @extend_schema_field(CitizenSummarySerializer(allow_null=True))
    def get_citizen(self, obj):
        """The phone behind a citizen post (contract section 15); null on officials' posts."""
        if not obj.citizen_id:
            return None
        from apps.citizens.services import active_block, same_phone

        summaries = self.context.setdefault("citizen_summaries", {})
        if obj.citizen_id not in summaries:
            posts = Post.objects.filter(citizen__in=same_phone(obj.citizen))
            block = active_block(obj.citizen)
            summaries[obj.citizen_id] = {
                "device_id": str(obj.citizen_id),
                "blocked": block is not None,
                "blocked_until": serializers.DateTimeField().to_representation(block.blocked_until)
                if block and block.blocked_until else None,
                "posts_total": posts.count(),
                "posts_discarded": posts.filter(status=PostStatus.DISCARDED).count(),
            }
        return summaries[obj.citizen_id]

    def get_allowed_actions(self, obj) -> list[str]:
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return []
        return allowed_actions(request.user, obj)


def prime_posts(context, posts) -> None:
    describer_from(context).prime([ref for post in posts for ref in (post.target, post.posted_as)])


class PostWriteSerializer(serializers.Serializer):
    category = serializers.SlugRelatedField(slug_field="key", queryset=Category.objects.filter(is_active=True))
    language = serializers.ChoiceField(choices=["te", "en"], default="te")
    title = serializers.CharField(max_length=150)
    body = serializers.CharField(max_length=5000)
    target = AreaInputSerializer()
    event = EventSerializer(required=False, allow_null=True, default=None)
    is_urgent = serializers.BooleanField(default=False)
    submit = serializers.BooleanField(default=True)

    def validate_title(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError(_("Enter a title."))
        return value

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError(_("Enter the post text."))
        return value.strip()


class PostUpdateSerializer(PostWriteSerializer):
    submit = None
    event = EventSerializer(required=False, allow_null=True)
    is_urgent = serializers.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        kwargs["partial"] = True
        super().__init__(*args, **kwargs)


class ReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["publish", "recheck", "discard", "unpublish", "reverse"])
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    to_status = serializers.ChoiceField(choices=list(REVERSE_TARGETS), required=False)


class MediaUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    kind = serializers.ChoiceField(choices=MediaKind.choices, default=MediaKind.IMAGE)
    #: Video only: the length when the server cannot measure it, and a poster photo
    #: when it cannot cut one (no ffprobe or ffmpeg on the machine).
    duration_s = serializers.FloatField(required=False, min_value=0.1)
    poster = serializers.FileField(required=False)


class UrgentSerializer(serializers.Serializer):
    is_urgent = serializers.BooleanField()


class TargetLevelSerializer(serializers.Serializer):
    level = serializers.CharField()
    areas = AreaOutputSerializer(many=True)
    pick_via = serializers.CharField(required=False)


class TargetsSerializer(serializers.Serializer):
    levels = TargetLevelSerializer(many=True)


class SettingsSerializer(serializers.Serializer):
    review_escalation_hours = serializers.IntegerField(min_value=6, max_value=168)
    languages = serializers.DictField(child=serializers.BooleanField())
    #: Posts one citizen phone may send in 24 hours; 0 is no limit (contract section 15).
    citizen_posts_per_day = serializers.IntegerField(min_value=0, max_value=1000)
    admin_posts_auto_publish = serializers.BooleanField(read_only=True)
    review_first_decision_wins = serializers.BooleanField(read_only=True)
    super_admin_can_post = serializers.BooleanField(read_only=True)
    max_photos_per_post = serializers.IntegerField(read_only=True, default=settings.MAX_PHOTOS_PER_POST)

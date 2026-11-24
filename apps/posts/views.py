"""Posts API: docs/api-contract.md, sections 5 and 10."""

from django.conf import settings
from django.db import transaction
from django.db.models import BooleanField, Case, Count, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import authz
from apps.accounts.models import RoleAssignment, User
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.audit.services import record
from apps.citizens import services as citizen_services
from apps.geography.areas import AreaRef, within_q
from apps.geography.models import AreaLevel
from grama import logos
from grama.pagination import use_known_count
from grama.exceptions import ApiError, out_of_area, permission_denied
from grama.params import UUID_REGEX, is_number, uuid_or_none

from . import media as media_service
from . import services
from .models import Category, Post, PostMedia, PostStatus, ReviewAction, SettingOverride
from .serializers import (
    AdminCategorySerializer,
    CategoryListSerializer,
    CitizenBlockSerializer,
    CitizenDiscardAllSerializer,
    MediaSerializer,
    MediaUploadSerializer,
    PostSerializer,
    PostUpdateSerializer,
    PostWriteSerializer,
    ReviewActionSerializer,
    ReviewSerializer,
    SettingsSerializer,
    TargetsSerializer,
    UrgentSerializer,
    prime_posts,
)


def post_queryset(queryset):
    return queryset.select_related("author", "citizen", "category", "decided_by").prefetch_related(
        "media", "translations", "targets", Prefetch("review_actions", queryset=ReviewAction.objects.select_related("actor"))
    )


def already_decided_error(post: Post, context) -> ApiError:
    decided_by = PostSerializer(post, context=context).data
    last = next((a for a in post.review_actions.all() if a.action != ReviewAction.Action.ESCALATE), None)
    return ApiError(
        "already_decided",
        _("Another reviewer already acted on this post."),
        status=status.HTTP_409_CONFLICT,
        decided_by=decided_by["decided_by"],
        action=last.action if last else post.status,
        at=decided_by["decided_at"],
        post=decided_by,
    )


# --- Categories -------------------------------------------------------------------------


@method_decorator(cache_control(public=True, max_age=3600), name="dispatch")
class CategoryListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    pagination_class = None
    serializer_class = CategoryListSerializer
    queryset = Category.objects.filter(is_active=True)


@method_decorator(cache_control(public=True, max_age=300), name="dispatch")
class AppConfigView(APIView):
    """What the apps need before login: the languages the Super Admin left on, the app update rules
    min_version / latest_version (BVTGC-90) and the push topic prefix (BVTGC-69), from the server's
    settings (contract section 5).

    Only these: escalation hours and the citizens' daily limit stay Super Admin only. No login, and a
    stale token is ignored, like /categories/. max-age 300: a switch reaches phones within 5 minutes.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="Public app settings: the languages switched on, and the app update rules",
        responses=inline_serializer(
            "AppConfig",
            {
                "languages": serializers.DictField(child=serializers.BooleanField()),
                "min_version": serializers.CharField(allow_null=True),
                "latest_version": serializers.CharField(allow_null=True),
                "topic_prefix": serializers.CharField(),
                "ads_banner_limit": serializers.IntegerField(),
            },
        ),
    )
    def get(self, request):
        return Response(
            {
                "languages": {"te": True, "en": bool(services.runtime("LANGUAGE_EN_ENABLED"))},
                # App updates (BVTGC-90): "1.0.0+24" or null; the app compares the build numbers only.
                "min_version": settings.APP_MIN_VERSION or None,
                "latest_version": settings.APP_LATEST_VERSION or None,
                # Push topics (BVTGC-69): the prefix this server sends area topics with ("qa_" on qa,
                # "" in production). The app subscribes to prefix + "village_12" and so on, so an app
                # talking to qa hears qa's posts and never production's, and the other way round.
                "topic_prefix": settings.FCM_TOPIC_PREFIX,
                # Banners on a citizen's Home (BVTGC-92): the Super Admin's number, 0 = no limit. Citizens
                # cannot read /admin/settings/, and the app follows the limit itself too.
                "ads_banner_limit": int(services.runtime("ADS_PUBLIC_LIMIT")),
            }
        )


def _icon_removed(request) -> bool:
    """`"icon": null` in JSON, or an empty `icon` field in a form, takes the icon away."""
    return "icon" in request.data and not request.FILES.get("icon") and request.data.get("icon") in (None, "")


def _new_icon(request) -> bytes | None:
    """The uploaded `icon` as WebP bytes (grama.logos: type, size and pixels checked), or None; 400 on `icon`."""
    upload = request.FILES.get("icon")
    return logos.process_logo(upload, field="icon") if upload is not None else None


class AdminCategoryViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Super Admin categories. JSON, or multipart with an `icon` image (BVTGC-91): POST adds the icon,
    PATCH replaces it, and `"icon": null` on PATCH removes it. A replaced or removed file is deleted
    only after the change is saved."""

    permission_classes = [IsAuthenticated, HasPerm("posts.manage_categories", PLATFORM, gettext_lazy("Only Super Admins can do this."))]
    serializer_class = AdminCategorySerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Category.objects.annotate(post_count=Count("posts")).order_by("order", "id")

    # Each change and its audit entry are saved together or not at all (CLAUDE.md, audit rule).
    @transaction.atomic
    def perform_create(self, serializer):
        icon = _new_icon(self.request)  # checked before anything is saved
        category = serializer.save()
        if icon is not None:
            logos.store_logo(category.icon, category.key, icon)
            category.save(update_fields=["icon"])
        record("category.created", actor=self.request.user, target=category, target_label=category.name_en,
               via="console", data={"key": category.key, "icon": icon is not None})

    @transaction.atomic
    def perform_update(self, serializer):
        icon = _new_icon(self.request)
        remove = icon is None and _icon_removed(self.request) and bool(serializer.instance.icon)
        category = serializer.save()
        # Only the fields the serializer accepted and checked, never the raw request body.
        changes = dict(serializer.validated_data)
        if icon is not None or remove:
            logos.delete_after_commit(category.icon.name, Category._meta.get_field("icon").storage)
            if icon is not None:
                logos.store_logo(category.icon, category.key, icon)
            else:
                category.icon = ""
            category.save(update_fields=["icon"])
            changes["icon"] = "replaced" if icon is not None else "removed"
        record(
            "category.updated", actor=self.request.user, target=category, target_label=category.name_en,
            via="console", data=changes,
        )


# --- Posts ----------------------------------------------------------------------------------

POST_FILTERS = [
    OpenApiParameter("status", OpenApiTypes.STR, description="Comma list, e.g. PENDING,PUBLISHED"),
    OpenApiParameter("mine", OpenApiTypes.BOOL),
    OpenApiParameter("author", OpenApiTypes.UUID),
    OpenApiParameter("category", OpenApiTypes.STR, description="Category key"),
    OpenApiParameter("district", OpenApiTypes.INT),
    OpenApiParameter("level", OpenApiTypes.STR, enum=[lv.value for lv in AreaLevel]),
    OpenApiParameter("area_id", OpenApiTypes.INT),
    OpenApiParameter("target_level", OpenApiTypes.STR, enum=[lv.value for lv in AreaLevel]),
    OpenApiParameter("escalated", OpenApiTypes.BOOL),
    OpenApiParameter("q", OpenApiTypes.STR, description="Search title and body"),
    OpenApiParameter(
        "source", OpenApiTypes.STR, enum=["official", "citizen"],
        description="official: posts by officials; citizen: citizen posts (contract section 15)",
    ),
    OpenApiParameter("citizen", OpenApiTypes.UUID, description="Super Admin: every post of this citizen's phone"),
]


SOURCE_PARAM = OpenApiParameter(
    "source", OpenApiTypes.STR, enum=["official", "citizen"],
    description="official: posts by officials; citizen: citizen posts (contract section 15)",
)
SOURCE_Q = {"official": Q(citizen__isnull=True), "citizen": Q(citizen__isnull=False)}


def source_filter(request, queryset):
    """Citizen posts in their own queue (lead's decision of 2026-10-27): ?source=official|citizen."""
    source = request.query_params.get("source", "").strip().lower()
    if not source:
        return queryset
    if source not in SOURCE_Q:
        raise ApiError("validation_error", _("Unknown source."), fields={"source": [_("official or citizen")]})
    return queryset.filter(SOURCE_Q[source])


def source_counts(queryset) -> dict:
    """{"official": n, "citizen": n} with one grouped count."""
    is_citizen = Case(When(citizen__isnull=False, then=Value(True)), default=Value(False), output_field=BooleanField())
    rows = queryset.order_by().annotate(is_citizen=is_citizen).values("is_citizen").annotate(n=Count("id"))
    found = {bool(row["is_citizen"]): row["n"] for row in rows}
    return {"official": found.get(False, 0), "citizen": found.get(True, 0)}


def area_filter(request, queryset):
    params = request.query_params
    level, area_id = params.get("level"), params.get("area_id")
    if params.get("district"):
        level, area_id = AreaLevel.DISTRICT, params["district"]
    if level or area_id:
        if level not in AreaLevel.values or not is_number(area_id):
            raise ApiError("validation_error", _("Give both level and area_id."), fields={"area_id": [_("Required with level.")]})
        queryset = queryset.filter(services.targets_within(AreaRef(level, int(area_id))))
    return queryset


def citizen_filter(user, raw, queryset):
    """?citizen=<id>: every post of that phone, all its installs (Super Admin only; owner, 2026-11-21).

    Which posts one phone wrote is the Super Admin's to see (they block phones), so anyone else gets
    403 permission_denied. Any install's id works; the blocked citizens list gives the first one.
    """
    if not authz.can(user, services.BLOCK_CITIZEN):
        raise permission_denied(_("Only the Super Admin can list a citizen's posts."))
    device_id = uuid_or_none(raw)
    if device_id is None:
        raise ApiError("validation_error", _("Give a citizen id."), fields={"citizen": [_("Not a citizen id.")]})
    device = citizen_services.device_or_404(device_id)
    return queryset.filter(citizen__in=citizen_services.same_phone(device))


class PostViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PostSerializer
    lookup_value_regex = UUID_REGEX  # /posts/abc/ is a 404, not a crash

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # drf-spectacular: the model, for the {id} type
            return Post.objects.none()
        return post_queryset(services.visible_posts(self.request.user))

    def get_object(self) -> Post:
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    def _out(self, post, code=status.HTTP_200_OK):
        context = self.get_serializer_context()
        prime_posts(context, [post])
        post = post_queryset(Post.objects.filter(pk=post.pk)).get()
        return Response(PostSerializer(post, context=context).data, status=code)

    def _filters_except_status_and_source(self, queryset):
        params = self.request.query_params
        if params.get("mine") in ("1", "true", "True"):
            queryset = queryset.filter(author=self.request.user)
        if params.get("author"):
            author = uuid_or_none(params["author"])
            if author is None:
                raise ApiError("validation_error", _("Give a user id."), fields={"author": [_("Not a user id.")]})
            queryset = queryset.filter(author_id=author)
        if params.get("category"):
            queryset = queryset.filter(category__key=params["category"])
        if params.get("target_level"):
            queryset = queryset.filter(services.targets_match(Q(area_level=params["target_level"])))
        if params.get("escalated") in ("1", "true", "True"):
            queryset = queryset.filter(status=PostStatus.PENDING, escalated_at__isnull=False)
        query = params.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(title__icontains=query) | Q(body__icontains=query))
        if params.get("citizen"):
            queryset = citizen_filter(self.request.user, params["citizen"], queryset)
        return area_filter(self.request, queryset)

    def _by_status(self, queryset, wanted: list[str]):
        if wanted:
            return queryset.filter(status__in=wanted)
        return queryset.exclude(Q(status=PostStatus.DRAFT) & ~Q(author=self.request.user))

    @extend_schema(summary="Posts you can see", parameters=POST_FILTERS)
    def list(self, request):
        wanted = [s.strip().upper() for s in request.query_params.get("status", "").split(",") if s.strip()]
        unknown = set(wanted) - set(PostStatus.values)
        if unknown:
            raise ApiError("validation_error", _("Unknown status."), fields={"status": sorted(unknown)})
        unsourced = self._filters_except_status_and_source(self.get_queryset())
        base = source_filter(request, unsourced)  # also refuses an unknown source
        source = request.query_params.get("source", "").strip().lower()
        # One grouped count by status and by source gives every number below (audit finding BE-DB-009: one
        # COUNT per page view). visible_posts() holds no one else's drafts, so the list's draft exclude
        # (_by_status) removes nothing from these counts.
        is_citizen = Case(When(citizen__isnull=False, then=Value(True)), default=Value(False), output_field=BooleanField())
        rows = [
            (row["status"], "citizen" if row["is_citizen"] else "official", row["n"])
            for row in unsourced.order_by().annotate(is_citizen=is_citizen)
            .values("status", "is_citizen").annotate(n=Count("id"))
        ]
        counts: dict[str, int] = {}
        for status_value, kind, n in rows:
            if not source or kind == source:
                counts[status_value] = counts.get(status_value, 0) + n
        status_counts = {"ALL": sum(n for s, n in counts.items() if s != PostStatus.DRAFT)}
        status_counts.update({value: counts.get(value, 0) for value in PostStatus.values})
        # The All / Officials / Citizens numbers (2026-11-20): every filter but `source`, the status included, so
        # they match the list under the chosen status tab.
        by_source = {"official": 0, "citizen": 0}
        for status_value, kind, n in rows:
            if not wanted or status_value in wanted:
                by_source[kind] += n
        queryset = self._by_status(base, wanted)
        if wanted:
            total = sum(counts.get(value, 0) for value in set(wanted))
        else:
            total = sum(counts.values())
        # The page total comes from the status counts above: no second COUNT(*) (audit finding BE-DB-009).
        use_known_count(self.paginator, total)
        page = self.paginate_queryset(queryset.order_by("-created_at", "id"))
        context = self.get_serializer_context()
        prime_posts(context, page)
        response = self.get_paginated_response(PostSerializer(page, many=True, context=context).data)
        response.data["status_counts"] = status_counts
        response.data["source_counts"] = by_source
        return response

    @extend_schema(summary="Write a post", request=PostWriteSerializer, responses={201: PostSerializer, 200: PostSerializer})
    def create(self, request):
        data = PostWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        post = services.create_post(
            request.user,
            services.PostInput(
                category=values["category"], language=values["language"], title=values["title"],
                body=values["body"], target=values.get("target"), targets=values.get("targets"),
                event=values.get("event"), is_urgent=values["is_urgent"],
            ),
            submit=values["submit"],
            client_ref=values.get("client_ref"),
        )
        # 200 for a repeat of an earlier create (same client_ref): the post as it is now.
        return self._out(post, status.HTTP_200_OK if getattr(post, "repeated", False) else status.HTTP_201_CREATED)

    @extend_schema(summary="One post")
    def retrieve(self, request, pk=None):
        return self._out(self.get_object())

    @extend_schema(summary="Edit a draft or a post sent back for recheck", request=PostUpdateSerializer)
    def partial_update(self, request, pk=None):
        post = self.get_object()
        data = PostUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._out(services.update_post(request.user, post, data.validated_data))

    @extend_schema(summary="Delete a draft", responses={204: None})
    def destroy(self, request, pk=None):
        services.delete_post(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(summary="Submit a draft, or resubmit after recheck", request=None)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._out(services.submit_post(request.user, self.get_object()))

    @extend_schema(summary="Review history, newest first", responses=ReviewActionSerializer(many=True))
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        post = self.get_object()
        actions = post.review_actions.select_related("actor").order_by("-created_at")
        return Response(ReviewActionSerializer(actions, many=True).data)

    @extend_schema(summary="Publish, recheck, discard, take down or reverse", request=ReviewSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        post = self.get_object()
        data = ReviewSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            post = services.review(request.user, post, values["action"], values["comment"], values.get("to_status"))
        except services.AlreadyDecided as decided:
            context = self.get_serializer_context()
            prime_posts(context, [decided.post])
            current = post_queryset(Post.objects.filter(pk=decided.post.pk)).get()
            raise already_decided_error(current, context) from None
        return self._out(post)

    @extend_schema(summary="Add a photo or the video", request={"multipart/form-data": MediaUploadSerializer}, responses={201: MediaSerializer})
    @action(detail=True, methods=["post"], url_path="media", parser_classes=[MultiPartParser, FormParser])
    def add_media(self, request, pk=None):
        post = self.get_object()
        data = MediaUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if values["kind"] == "video":
            media = media_service.add_video(
                request.user, post, values["file"], duration_s=values.get("duration_s"), poster=values.get("poster")
            )
        else:
            media = media_service.add_photo(request.user, post, values["file"])
        return Response(MediaSerializer(media, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Mark or clear urgent (owner decision Q19)", request=UrgentSerializer, responses=PostSerializer)
    @action(detail=True, methods=["post"], url_path="urgent")
    def urgent(self, request, pk=None):
        post = self.get_object()
        data = UrgentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._out(services.set_urgent(request.user, post, data.validated_data["is_urgent"]))

    @extend_schema(
        summary="Super Admin: block the phone that wrote this citizen post for some days; its pending posts are discarded",
        request=CitizenBlockSerializer,
        responses=PostSerializer,
    )
    @action(detail=True, methods=["post"], url_path="block-citizen")
    def block_citizen(self, request, pk=None):
        post = self.get_object()
        data = CitizenBlockSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        citizen_services.block(request.user, post, data.validated_data["comment"], data.validated_data["days"])
        return self._out(post)

    @extend_schema(summary="Super Admin: lift the block on the phone that wrote this citizen post", request=None, responses=PostSerializer)
    @action(detail=True, methods=["post"], url_path="unblock-citizen")
    def unblock_citizen(self, request, pk=None):
        post = self.get_object()
        citizen_services.unblock(request.user, post)
        return self._out(post)

    @extend_schema(
        summary="Super Admin: discard every pending post of the phone that wrote this citizen post",
        request=CitizenDiscardAllSerializer,
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["post"], url_path="discard-citizen-posts")
    def discard_citizen_posts(self, request, pk=None):
        post = self.get_object()
        data = CitizenDiscardAllSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response({"discarded": citizen_services.discard_all(request.user, post, data.validated_data["comment"])})

    @extend_schema(summary='"Visible to" choices for compose (owner decision Q5)', responses=TargetsSerializer)
    @action(detail=False, methods=["get"], url_path="targets")
    def targets(self, request):
        return Response(services.posting_targets(request.user))

    @extend_schema(summary="Remove a photo or the video", responses={204: None})
    @action(detail=True, methods=["delete"], url_path=rf"media/(?P<media_id>{UUID_REGEX})")
    def remove_media(self, request, pk=None, media_id=None):
        post = self.get_object()
        media = get_object_or_404(PostMedia, pk=media_id, post=post)
        media_service.remove_photo(request.user, post, media)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Review queue, stats, settings ------------------------------------------------------------


class ReviewQueueView(generics.ListAPIView):
    permission_classes = [IsAuthenticated, HasPerm("posts.view_review_queue", message=gettext_lazy("Your role cannot review posts."))]
    serializer_class = PostSerializer

    @extend_schema(
        summary="Pending posts inside your areas, oldest first",
        parameters=[
            OpenApiParameter("level", OpenApiTypes.STR), OpenApiParameter("area_id", OpenApiTypes.INT),
            OpenApiParameter("category", OpenApiTypes.STR), OpenApiParameter("escalated", OpenApiTypes.BOOL),
            SOURCE_PARAM,
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def _queue(self):
        """The queue with every filter except source, for the tab counts."""
        queryset = services.review_queue(self.request.user)
        params = self.request.query_params
        if params.get("category"):
            queryset = queryset.filter(category__key=params["category"])
        if params.get("escalated") in ("1", "true", "True"):
            queryset = queryset.filter(escalated_at__isnull=False)
        return area_filter(self.request, queryset)

    def get_queryset(self):
        return post_queryset(source_filter(self.request, self._queue()))

    def list(self, request, *args, **kwargs):
        # One grouped count gives the tab counts and the page total (audit finding BE-DB-009).
        counts = source_counts(self._queue())
        source = request.query_params.get("source", "").strip().lower()
        use_known_count(self.paginator, counts.get(source, 0) if source else sum(counts.values()))
        page = self.paginate_queryset(self.get_queryset())
        context = self.get_serializer_context()
        prime_posts(context, page)
        response = self.get_paginated_response(PostSerializer(page, many=True, context=context).data)
        response.data["source_counts"] = counts
        return response


class StatsView(APIView):
    permission_classes = [IsAuthenticated, HasPerm("posts.view_stats")]

    @extend_schema(
        summary="Counts for an area dashboard",
        parameters=[OpenApiParameter("level", OpenApiTypes.STR), OpenApiParameter("area_id", OpenApiTypes.INT)],
        responses=OpenApiTypes.OBJECT,  # docs/api-contract.md, "GET /stats"
    )
    def get(self, request):
        perm = "posts.view_stats"
        params = request.query_params
        if params.get("level") or params.get("area_id"):
            if params.get("level") not in AreaLevel.values or not is_number(params.get("area_id")):
                raise ApiError("validation_error", _("Give both level and area_id."))
            area = AreaRef(params["level"], int(params["area_id"]))
            if not authz.can(request.user, perm, area):
                raise out_of_area()
        else:
            area = authz.best_grant(request.user, perm).area
        global_view = area.level == AreaLevel.STATE and authz.can(request.user, perm, PLATFORM)
        posts = Post.objects.all() if global_view else Post.objects.filter(services.targets_within(area))
        today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        assignment_q = Q() if global_view else within_q(area)
        users = User.objects.filter(id__in=RoleAssignment.objects.filter(assignment_q).values("user_id"))
        return Response(
            {
                "area": {"level": area.level, "id": area.id},
                "pending_review": posts.filter(status=PostStatus.PENDING).count(),
                "published_today": posts.filter(status=PostStatus.PUBLISHED, published_at__gte=today).count(),
                "active_users": users.filter(is_active=True).count(),
                "blocked_users": users.filter(is_active=False).count(),
                "escalated": posts.filter(status=PostStatus.PENDING, escalated_at__isnull=False).count(),
            }
        )


def current_settings() -> dict:
    from django.conf import settings

    return {
        "review_escalation_hours": int(services.runtime("REVIEW_ESCALATION_HOURS")),
        "languages": {"te": True, "en": bool(services.runtime("LANGUAGE_EN_ENABLED"))},
        "citizen_posts_per_day": int(services.runtime("CITIZEN_POSTS_PER_DAY")),
        "ads_banner_limit": int(services.runtime("ADS_PUBLIC_LIMIT")),  # 0 = no limit (BVTGC-92)
        "admin_posts_auto_publish": settings.ADMIN_POSTS_AUTO_PUBLISH,
        "review_first_decision_wins": settings.REVIEW_FIRST_DECISION_WINS,
        "super_admin_can_post": settings.SUPER_ADMIN_CAN_POST,
        "max_photos_per_post": settings.MAX_PHOTOS_PER_POST,
    }


class AdminSettingsView(APIView):
    permission_classes = [IsAuthenticated, HasPerm("posts.manage_settings", PLATFORM, gettext_lazy("Only Super Admins can do this."))]

    @extend_schema(summary="Platform settings", responses=SettingsSerializer)
    def get(self, request):
        return Response(current_settings())

    @extend_schema(
        summary="Change escalation hours, switch English, set the citizen posts per day or the banners per Home",
        request=SettingsSerializer,
        responses=SettingsSerializer,
    )
    def patch(self, request):
        data = SettingsSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        before = current_settings()
        changes = {}
        if "review_escalation_hours" in data.validated_data:
            changes["REVIEW_ESCALATION_HOURS"] = data.validated_data["review_escalation_hours"]
        if "citizen_posts_per_day" in data.validated_data:
            changes["CITIZEN_POSTS_PER_DAY"] = data.validated_data["citizen_posts_per_day"]
        if "ads_banner_limit" in data.validated_data:
            changes["ADS_PUBLIC_LIMIT"] = data.validated_data["ads_banner_limit"]
        languages = data.validated_data.get("languages")
        if languages is not None:
            if languages.get("te") is False:
                raise ApiError("validation_error", _("Telugu cannot be switched off."), fields={"languages": [_("Telugu is required.")]})
            if "en" in languages:
                changes["LANGUAGE_EN_ENABLED"] = languages["en"]
        # The settings and their audit entry are saved together or not at all.
        with transaction.atomic():
            for key, value in changes.items():
                SettingOverride.objects.update_or_create(key=key, defaults={"value": value, "updated_by": request.user})
            after = current_settings()
            if changes:
                record("setting.changed", actor=request.user, via="console", data={"before": before, "after": after})
        return Response(after)

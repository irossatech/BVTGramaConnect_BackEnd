"""Posts API: docs/api-contract.md, sections 5 and 10."""

from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
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
from grama.exceptions import ApiError, out_of_area

from . import media as media_service
from . import services
from .models import Category, Post, PostMedia, PostStatus, ReviewAction, SettingOverride
from .serializers import (
    AdminCategorySerializer,
    CategorySerializer,
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
        "media", Prefetch("review_actions", queryset=ReviewAction.objects.select_related("actor"))
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
    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)


class AdminCategoryViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, HasPerm("posts.manage_categories", PLATFORM, gettext_lazy("Only Super Admins can do this."))]
    serializer_class = AdminCategorySerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Category.objects.annotate(post_count=Count("posts")).order_by("order", "id")

    def perform_create(self, serializer):
        category = serializer.save()
        record("category.created", actor=self.request.user, target=category, target_label=category.name_en, via="console")

    def perform_update(self, serializer):
        category = serializer.save()
        record(
            "category.updated", actor=self.request.user, target=category, target_label=category.name_en,
            via="console", data=dict(self.request.data),
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
    return {name: queryset.filter(query).count() for name, query in SOURCE_Q.items()}


def area_filter(request, queryset):
    params = request.query_params
    level, area_id = params.get("level"), params.get("area_id")
    if params.get("district"):
        level, area_id = AreaLevel.DISTRICT, params["district"]
    if level or area_id:
        if level not in AreaLevel.values or not (area_id or "").isdigit():
            raise ApiError("validation_error", _("Give both level and area_id."), fields={"area_id": [_("Required with level.")]})
        queryset = queryset.filter(within_q(AreaRef(level, int(area_id)), level_field="target_level", id_field="target_area_id"))
    return queryset


class PostViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PostSerializer

    def get_queryset(self):
        return post_queryset(services.visible_posts(self.request.user))

    def get_object(self) -> Post:
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

    def _out(self, post, code=status.HTTP_200_OK):
        context = self.get_serializer_context()
        prime_posts(context, [post])
        post = post_queryset(Post.objects.filter(pk=post.pk)).get()
        return Response(PostSerializer(post, context=context).data, status=code)

    def _filters_except_status(self, queryset):
        params = self.request.query_params
        if params.get("mine") in ("1", "true", "True"):
            queryset = queryset.filter(author=self.request.user)
        if params.get("author"):
            queryset = queryset.filter(author_id=params["author"])
        if params.get("category"):
            queryset = queryset.filter(category__key=params["category"])
        if params.get("target_level"):
            queryset = queryset.filter(target_level=params["target_level"])
        if params.get("escalated") in ("1", "true", "True"):
            queryset = queryset.filter(status=PostStatus.PENDING, escalated_at__isnull=False)
        query = params.get("q", "").strip()
        if query:
            queryset = queryset.filter(Q(title__icontains=query) | Q(body__icontains=query))
        return source_filter(self.request, area_filter(self.request, queryset))

    @extend_schema(summary="Posts you can see", parameters=POST_FILTERS)
    def list(self, request):
        base = self._filters_except_status(self.get_queryset())
        counts = {row["status"]: row["n"] for row in base.order_by().values("status").annotate(n=Count("id"))}
        status_counts = {"ALL": sum(n for s, n in counts.items() if s != PostStatus.DRAFT)}
        status_counts.update({value: counts.get(value, 0) for value in PostStatus.values})
        queryset = base
        wanted = [s.strip().upper() for s in request.query_params.get("status", "").split(",") if s.strip()]
        if wanted:
            unknown = set(wanted) - set(PostStatus.values)
            if unknown:
                raise ApiError("validation_error", _("Unknown status."), fields={"status": sorted(unknown)})
            queryset = queryset.filter(status__in=wanted)
        else:
            queryset = queryset.exclude(Q(status=PostStatus.DRAFT) & ~Q(author=request.user))
        page = self.paginate_queryset(queryset.order_by("-created_at", "id"))
        context = self.get_serializer_context()
        prime_posts(context, page)
        response = self.get_paginated_response(PostSerializer(page, many=True, context=context).data)
        response.data["status_counts"] = status_counts
        return response

    @extend_schema(summary="Write a post", request=PostWriteSerializer, responses={201: PostSerializer})
    def create(self, request):
        data = PostWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        post = services.create_post(
            request.user,
            services.PostInput(
                category=values["category"], language=values["language"], title=values["title"],
                body=values["body"], target=values["target"],
                event=values.get("event"), is_urgent=values["is_urgent"],
            ),
            submit=values["submit"],
        )
        return self._out(post, status.HTTP_201_CREATED)

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
    @action(detail=True, methods=["delete"], url_path=r"media/(?P<media_id>[0-9a-f-]+)")
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
        page = self.paginate_queryset(self.get_queryset())
        context = self.get_serializer_context()
        prime_posts(context, page)
        response = self.get_paginated_response(PostSerializer(page, many=True, context=context).data)
        response.data["source_counts"] = source_counts(self._queue())
        return response


class StatsView(APIView):
    permission_classes = [IsAuthenticated, HasPerm("posts.view_stats")]

    @extend_schema(
        summary="Counts for an area dashboard",
        parameters=[OpenApiParameter("level", OpenApiTypes.STR), OpenApiParameter("area_id", OpenApiTypes.INT)],
    )
    def get(self, request):
        perm = "posts.view_stats"
        params = request.query_params
        if params.get("level") or params.get("area_id"):
            if params.get("level") not in AreaLevel.values or not (params.get("area_id") or "").isdigit():
                raise ApiError("validation_error", _("Give both level and area_id."))
            area = AreaRef(params["level"], int(params["area_id"]))
            if not authz.can(request.user, perm, area):
                raise out_of_area()
        else:
            area = authz.best_grant(request.user, perm).area
        global_view = area.level == AreaLevel.STATE and authz.can(request.user, perm, PLATFORM)
        target_q = Q() if global_view else within_q(area, level_field="target_level", id_field="target_area_id")
        posts = Post.objects.filter(target_q)
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
        summary="Change escalation hours, switch English, or set the citizen posts per day",
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
        languages = data.validated_data.get("languages")
        if languages is not None:
            if languages.get("te") is False:
                raise ApiError("validation_error", _("Telugu cannot be switched off."), fields={"languages": [_("Telugu is required.")]})
            if "en" in languages:
                changes["LANGUAGE_EN_ENABLED"] = languages["en"]
        for key, value in changes.items():
            SettingOverride.objects.update_or_create(key=key, defaults={"value": value, "updated_by": request.user})
        after = current_settings()
        if changes:
            record("setting.changed", actor=request.user, via="console", data={"before": before, "after": after})
        return Response(after)

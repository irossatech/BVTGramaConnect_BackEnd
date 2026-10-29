"""Public citizen feed. No login. docs/api-contract.md, section 6.

GET /api/v1/feed/?village_id=12&cursor=&category=news,updates
    Published posts aimed at the village, its mandal, its district or its
    state, newest first, 20 per cursor page.
GET /api/v1/feed/posts/<id>/      one published post with all its photos
GET /api/v1/feed/posts/?ids=a,b   published posts saved on the phone
GET /api/v1/feed/upcoming/?village_id=12   events from today on ("Don't miss")
GET /api/v1/feed/urgent/?village_id=12     urgent posts of the last 7 days
The feed also takes q= (news search) and has_video=1.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.geography.areas import AreaDescriber, AreaRef, ancestry
from apps.geography.models import AreaLevel, Village
from grama.exceptions import ApiError
from apps.posts.models import MediaKind, Post, PostMedia, PostStatus
from apps.posts.services import targets_match

from .serializers import FeedItemSerializer, FeedPostSerializer

MAX_IDS = 50
public_cache = method_decorator(cache_control(public=True, max_age=60), name="dispatch")


class FeedCursorPagination(CursorPagination):
    ordering = ("-published_at", "-id")
    cursor_query_param = "cursor"

    def get_page_size(self, request):
        return settings.FEED_PAGE_SIZE


def published_posts():
    media = PostMedia.objects.filter(post=OuterRef("pk"))
    first_thumb = media.order_by("order", "created_at").values("thumbnail")[:1]
    # The first photo for big cards, at full size (1280 px): the 640 px file looked soft on
    # 1080 px phone screens. A video's poster stays in video.poster_url.
    first_image = media.filter(kind=MediaKind.IMAGE).order_by("order", "created_at").values("image")[:1]
    video = media.filter(kind=MediaKind.VIDEO)
    return (
        Post.objects.filter(status=PostStatus.PUBLISHED)
        .select_related("category")
        .prefetch_related("translations", "targets")
        .annotate(
            thumbnail_name=Subquery(first_thumb),
            image_name=Subquery(first_image),
            media_count=Count("media"),
            video_name=Subquery(video.values("video")[:1]),
            video_poster_name=Subquery(video.values("image")[:1]),
            video_duration=Subquery(video.values("duration_s")[:1]),
        )
    )


def village_from(request) -> int:
    raw = request.query_params.get("village_id", "").strip()
    if not raw.isdigit():
        raise ApiError(
            "validation_error",
            "The 'village_id' query parameter is required.",
            fields={"village_id": ["Give a village id."]},
        )
    village_id = int(raw)
    if not Village.objects.active().filter(id=village_id).exists():
        raise ApiError("not_found", "No such village.", status=404)
    return village_id


def village_feed(village_id: int):
    """Published posts a citizen of this village sees."""
    return published_posts().filter(targets_for_village(village_id))


def targets_for_village(village_id: int) -> Exists:
    """Posts with any area that is the village or above it (its mandal, district or the state)."""
    chain = ancestry(AreaRef(AreaLevel.VILLAGE, village_id))
    query = Q(pk__in=[])
    for level, area_id in chain.items():
        query |= Q(area_level=level, area_id=area_id)
    return targets_match(query)


class PublicView:
    permission_classes = [AllowAny]
    authentication_classes: list = []


def describer_with(posts) -> AreaDescriber:
    describer = AreaDescriber()
    describer.prime([ref for post in posts for ref in (post.target, post.posted_as, *post.target_refs)])
    return describer


@public_cache
@extend_schema(
    summary="Citizen feed for one village",
    parameters=[
        OpenApiParameter("village_id", OpenApiTypes.INT, required=True),
        OpenApiParameter("category", OpenApiTypes.STR, description="Category keys, comma separated"),
        OpenApiParameter("q", OpenApiTypes.STR, description="News search in title and body, 2+ characters"),
        OpenApiParameter("has_video", OpenApiTypes.BOOL, description="1: video posts only"),
        OpenApiParameter("cursor", OpenApiTypes.STR),
    ],
)
class FeedView(PublicView, generics.ListAPIView):
    serializer_class = FeedItemSerializer
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        params = self.request.query_params
        queryset = village_feed(village_from(self.request))
        categories = [key for key in params.get("category", "").split(",") if key.strip()]
        if categories:
            queryset = queryset.filter(category__key__in=[key.strip() for key in categories])
        query = " ".join(params.get("q", "").split())
        if query:
            if len(query) < settings.FEED_SEARCH_MIN_CHARS:
                raise ApiError(
                    "validation_error",
                    f"Search with at least {settings.FEED_SEARCH_MIN_CHARS} characters.",
                    fields={"q": [f"At least {settings.FEED_SEARCH_MIN_CHARS} characters."]},
                )
            queryset = queryset.filter(Q(title__icontains=query) | Q(body__icontains=query))
        if params.get("has_video") in ("1", "true", "True"):
            queryset = queryset.filter(
                Exists(PostMedia.objects.filter(post=OuterRef("pk"), kind=MediaKind.VIDEO))
            )
        return queryset

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        context = {**self.get_serializer_context(), "describer": describer_with(page)}
        return self.get_paginated_response(FeedItemSerializer(page, many=True, context=context).data)


@public_cache
class FeedPostDetailView(PublicView, APIView):
    @extend_schema(summary="One published post", responses=FeedPostSerializer)
    def get(self, request, pk):
        post = published_posts().prefetch_related("media").filter(pk=pk).first()
        if post is None:
            raise ApiError("not_found", "This post is not available.", status=404)
        return Response(FeedPostSerializer(post, context={"request": request, "describer": describer_with([post])}).data)


@public_cache
class FeedPostsByIdView(PublicView, APIView):
    @extend_schema(
        summary="Published posts by id (posts saved on the phone)",
        parameters=[OpenApiParameter("ids", OpenApiTypes.STR, required=True, description="Up to 50 ids, comma separated")],
        responses=FeedItemSerializer(many=True),
    )
    def get(self, request):
        ids = []
        for raw in request.query_params.get("ids", "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ids.append(uuid.UUID(raw))
            except ValueError:
                raise ApiError("validation_error", "Ids must be post ids.", fields={"ids": [raw]}) from None
        if not ids or len(ids) > MAX_IDS:
            raise ApiError(
                "validation_error", f"Give 1 to {MAX_IDS} ids.", fields={"ids": [f"Give 1 to {MAX_IDS} ids."]}
            )
        posts = {post.pk: post for post in published_posts().filter(pk__in=ids)}
        ordered = [posts[pk] for pk in ids if pk in posts]
        context = {"request": request, "describer": describer_with(ordered)}
        return Response(FeedItemSerializer(ordered, many=True, context=context).data)


def _short_list(request, queryset):
    posts = list(queryset)
    context = {"request": request, "describer": describer_with(posts)}
    return Response(FeedItemSerializer(posts, many=True, context=context).data)


VILLAGE_PARAM = [OpenApiParameter("village_id", OpenApiTypes.INT, required=True)]


@public_cache
class UpcomingView(PublicView, APIView):
    @extend_schema(summary='Events from today on, soonest first ("Don\'t miss")', parameters=VILLAGE_PARAM, responses=FeedItemSerializer(many=True))
    def get(self, request):
        today = timezone.localdate()
        queryset = (
            village_feed(village_from(request))
            .filter(event_start__isnull=False)
            .filter(Q(event_start__gte=today) | Q(event_end__gte=today))
            .order_by("event_start", "published_at", "id")[: settings.FEED_UPCOMING_LIMIT]
        )
        return _short_list(request, queryset)


@public_cache
class UrgentView(PublicView, APIView):
    @extend_schema(summary="Urgent posts of the last 7 days, newest first", parameters=VILLAGE_PARAM, responses=FeedItemSerializer(many=True))
    def get(self, request):
        since = timezone.now() - timedelta(days=settings.FEED_URGENT_DAYS)
        queryset = (
            village_feed(village_from(request))
            .filter(is_urgent=True, published_at__gte=since)
            .order_by("-published_at", "-id")[: settings.FEED_URGENT_LIMIT]
        )
        return _short_list(request, queryset)

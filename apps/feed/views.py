"""Public citizen feed. No login. docs/api-contract.md, section 6.

GET /api/v1/feed/?village_id=12&cursor=&category=news,updates
    Published posts aimed at the village, its mandal, its district or its
    state, newest first, 20 per cursor page.
GET /api/v1/feed/posts/<id>/      one published post with all its photos
GET /api/v1/feed/posts/?ids=a,b   published posts saved on the phone
"""

import uuid

from django.conf import settings
from django.db.models import Count, OuterRef, Q, Subquery
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
from apps.posts.models import Post, PostMedia, PostStatus

from .serializers import FeedItemSerializer, FeedPostSerializer

MAX_IDS = 50
public_cache = method_decorator(cache_control(public=True, max_age=60), name="dispatch")


class FeedCursorPagination(CursorPagination):
    ordering = ("-published_at", "-id")
    cursor_query_param = "cursor"

    def get_page_size(self, request):
        return settings.FEED_PAGE_SIZE


def published_posts():
    first_thumb = PostMedia.objects.filter(post=OuterRef("pk")).order_by("order", "created_at").values("thumbnail")[:1]
    return (
        Post.objects.filter(status=PostStatus.PUBLISHED)
        .select_related("category")
        .annotate(thumbnail_name=Subquery(first_thumb), media_count=Count("media"))
    )


def targets_for_village(village_id: int) -> Q:
    """Posts aimed at the village or any area above it."""
    chain = ancestry(AreaRef(AreaLevel.VILLAGE, village_id))
    query = Q(pk__in=[])
    for level, area_id in chain.items():
        query |= Q(target_level=level, target_area_id=area_id)
    return query


class PublicView:
    permission_classes = [AllowAny]
    authentication_classes: list = []


def describer_with(posts) -> AreaDescriber:
    describer = AreaDescriber()
    describer.prime([post.target for post in posts])
    return describer


@public_cache
@extend_schema(
    summary="Citizen feed for one village",
    parameters=[
        OpenApiParameter("village_id", OpenApiTypes.INT, required=True),
        OpenApiParameter("category", OpenApiTypes.STR, description="Category keys, comma separated"),
        OpenApiParameter("cursor", OpenApiTypes.STR),
    ],
)
class FeedView(PublicView, generics.ListAPIView):
    serializer_class = FeedItemSerializer
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        raw = self.request.query_params.get("village_id", "").strip()
        if not raw.isdigit():
            raise ApiError(
                "validation_error",
                "The 'village_id' query parameter is required.",
                fields={"village_id": ["Give a village id."]},
            )
        village_id = int(raw)
        if not Village.objects.active().filter(id=village_id).exists():
            raise ApiError("not_found", "No such village.", status=404)
        queryset = published_posts().filter(targets_for_village(village_id))
        categories = [key for key in self.request.query_params.get("category", "").split(",") if key.strip()]
        if categories:
            queryset = queryset.filter(category__key__in=[key.strip() for key in categories])
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

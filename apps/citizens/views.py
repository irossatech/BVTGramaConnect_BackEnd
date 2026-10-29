"""Citizen posting API: docs/api-contract.md, section 15.

No login: every call except the challenge and the registration carries the
phone's posting token in X-Citizen-Token. Officials' tokens and sessions are
not read here (authentication_classes is empty).
"""

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.posts.models import Post
from grama.http import client_ip

from . import integrity, services
from .serializers import (
    ChallengeSerializer,
    CitizenDeviceSerializer,
    CitizenPostSerializer,
    CitizenPostWriteSerializer,
    DeviceUpdateSerializer,
    RegisteredSerializer,
    RegisterSerializer,
)

TOKEN_PARAM = [OpenApiParameter("X-Citizen-Token", OpenApiTypes.STR, OpenApiParameter.HEADER, required=True)]
MY_POSTS_LIMIT = 50


class CitizenView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []


class ChallengeView(CitizenView):
    @extend_schema(summary="A one-time nonce for the Play Integrity request", request=None, responses=ChallengeSerializer)
    def post(self, request):
        return Response({"nonce": integrity.new_nonce(), "expires_in": settings.CITIZEN_CHALLENGE_SECONDS})


class RegisterView(CitizenView):
    @extend_schema(
        summary="Get this phone's posting token (shown once) after the Play Integrity check",
        request=RegisterSerializer,
        responses={201: RegisteredSerializer},
    )
    def post(self, request):
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        device, token = services.register(**data.validated_data, ip=client_ip(request))
        body = {"token": token, "device": CitizenDeviceSerializer(device).data}
        return Response(body, status=status.HTTP_201_CREATED)


class MeView(CitizenView):
    @extend_schema(summary="This phone: its village, block and today's posts", parameters=TOKEN_PARAM, responses=CitizenDeviceSerializer)
    def get(self, request):
        return Response(CitizenDeviceSerializer(services.device_from_request(request)).data)

    @extend_schema(
        summary="Change the village this phone posts to, its push token or its language",
        parameters=TOKEN_PARAM,
        request=DeviceUpdateSerializer,
        responses=CitizenDeviceSerializer,
    )
    def patch(self, request):
        device = services.device_from_request(request)
        data = DeviceUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(CitizenDeviceSerializer(services.update_device(device, **data.validated_data)).data)


def my_posts(device):
    return (
        Post.objects.filter(citizen=device)
        .select_related("category")
        .prefetch_related("media", "review_actions", "translations", "targets")
        .order_by("-created_at")
    )


class PostsView(CitizenView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="This phone's posts, newest first", parameters=TOKEN_PARAM, responses=CitizenPostSerializer(many=True))
    def get(self, request):
        device = services.device_from_request(request)
        return Response(CitizenPostSerializer(my_posts(device)[:MY_POSTS_LIMIT], many=True).data)

    @extend_schema(
        summary="Send a post for the village's officials to review",
        parameters=TOKEN_PARAM,
        request={"multipart/form-data": CitizenPostWriteSerializer},
        responses={201: CitizenPostSerializer},
    )
    def post(self, request):
        device = services.device_from_request(request)
        # Copy only the text fields: copying request.data deep-copies the uploaded files, which fails
        # for any file Django saved to a temporary file (over FILE_UPLOAD_MAX_MEMORY_SIZE, 2.5 MB).
        payload = request.POST.copy() if hasattr(request.data, "getlist") else dict(request.data)
        if hasattr(request.data, "getlist"):
            payload.setlist("photos", request.FILES.getlist("photos"))
            payload.setlist("videos", request.FILES.getlist("videos"))
        data = CitizenPostWriteSerializer(data=payload)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        post = services.create_post(
            device,
            category=values["category"],
            language=values["language"],
            title=values["title"],
            body=values["body"],
            photos=values["photos"],
            videos=values["videos"],
            targets=values.get("targets"),
        )
        post = my_posts(device).get(pk=post.pk)
        return Response(CitizenPostSerializer(post).data, status=status.HTTP_201_CREATED)

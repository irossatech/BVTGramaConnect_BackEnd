"""Dashboard and reports for Super Admins (docs/api-contract.md, section 8).

Used by the web console and the app's state console. Dates are bucketed in
Python in Asia/Kolkata time, so no database time-zone functions are needed
(MySQL without time-zone tables would otherwise return NULL).
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts import authz
from apps.accounts.models import RoleAssignment, User
from apps.accounts.permissions import PLATFORM, HasPerm
from apps.audit.models import AuditLog
from apps.audit.services import record
from apps.geography.areas import AreaDescriber, AreaRef
from apps.geography.models import AreaLevel, District, Mandal, Village
from grama.csvsafe import safe_writer
from grama.exceptions import ApiError, permission_denied
from grama.pagination import PageNumberPagination
from grama.renderers import JSONRenderer
from apps.posts.models import Post, PostStatus, ReviewAction, SettingOverride
from apps.posts.services import targets_within

from .permissions import client_via
from .transliteration import MAX_LENGTH, to_telugu

DECISIONS = [ReviewAction.Action.PUBLISH, ReviewAction.Action.RECHECK, ReviewAction.Action.DISCARD]


def area_admin_roles() -> list[str]:
    """Keys of the groups counted as area admins: they review posts and are not platform-wide."""
    return authz.role_keys_holding("posts.review_post", platform_wide=False)


def level_of_roles() -> dict[str, str]:
    """Role key -> the area level it is assigned at, from the role table."""
    return {key: role.level for key, role in authz.roles().items()}
REJECTED = [PostStatus.DISCARDED, PostStatus.UNPUBLISHED]


def local_date(value):
    return timezone.localtime(value).date() if value else None


def hours_since(value, now) -> float | None:
    return round((now - value).total_seconds() / 3600, 1) if value else None


def escalation_hours() -> int:
    override = SettingOverride.objects.filter(key="review_escalation_hours").first()
    try:
        return int(override.value) if override else settings.REVIEW_ESCALATION_HOURS
    except (TypeError, ValueError):
        return settings.REVIEW_ESCALATION_HOURS


def days_param(request, default=30) -> int:
    raw = request.query_params.get("days", str(default))
    if not raw.isdigit() or not 1 <= int(raw) <= 366:
        raise ApiError("validation_error", _("days must be between 1 and 366."), fields={"days": [_("1 to 366.")]})
    return int(raw)


def audit_entry(row: AuditLog, describer: AreaDescriber) -> dict:
    area = describer.describe(AreaRef(row.area_level, row.area_id)) if row.area_level and row.area_id else None
    return {
        "id": str(row.id),
        "at": row.created_at,
        "actor": {
            "id": str(row.actor_id) if row.actor_id else None,
            "name": row.actor_name or "System",
            "role": row.actor_role or "SYSTEM",
        },
        "action": row.action,
        "type": row.type,
        "target_label": row.target_label,
        "target": {"type": row.target_type, "id": row.target_id} if row.target_type else None,
        "area": area,
        "via": row.via,
    }


def posts_counts(queryset) -> dict:
    statuses = list(queryset.values_list("status", flat=True))
    return {
        "total": len(statuses),
        "published": sum(1 for s in statuses if s == PostStatus.PUBLISHED),
        "pending": sum(1 for s in statuses if s == PostStatus.PENDING),
        "rejected": sum(1 for s in statuses if s in REJECTED),
    }


class SuperAdminView(APIView):
    permission_classes = [IsAuthenticated, HasPerm("web_admin.view_reports", PLATFORM, "Only Super Admins can do this.")]


class DashboardView(SuperAdminView):
    permission_classes = [IsAuthenticated, HasPerm("web_admin.view_dashboard", PLATFORM, "Only Super Admins can do this.")]

    @extend_schema(summary="Super Admin dashboard")
    def get(self, request):
        now = timezone.now()
        today = local_date(now)
        describer = AreaDescriber()
        posts = Post.objects.exclude(status=PostStatus.DRAFT)
        pending = posts.filter(status=PostStatus.PENDING)

        pending_by_district = []
        for district in District.objects.active().order_by("name_en"):
            ref = AreaRef(AreaLevel.DISTRICT, district.id)
            inside = pending.filter(targets_within(ref))
            submitted = list(inside.values_list("submitted_at", flat=True))
            oldest = min((s for s in submitted if s), default=None)
            pending_by_district.append(
                {
                    "district": describer.describe(ref),
                    "name_te": district.name_te,
                    "pending": len(submitted),
                    "oldest_pending_hours": hours_since(oldest, now),
                }
            )
        pending_by_district.sort(key=lambda row: (-row["pending"], row["district"]["name"]))

        start = today - timedelta(days=13)
        window_start = now - timedelta(days=15)
        submitted_days = defaultdict(int)
        published_days = defaultdict(int)
        for value in posts.filter(submitted_at__gte=window_start).values_list("submitted_at", flat=True):
            submitted_days[local_date(value)] += 1
        for value in posts.filter(published_at__gte=window_start).values_list("published_at", flat=True):
            published_days[local_date(value)] += 1
        posts_per_day = [
            {"date": day.isoformat(), "submitted": submitted_days[day], "published": published_days[day]}
            for day in (start + timedelta(days=offset) for offset in range(14))
        ]

        published_today = sum(
            1 for value in posts.filter(published_at__gte=window_start).values_list("published_at", flat=True)
            if local_date(value) == today
        )
        oldest_pending = pending.order_by("submitted_at").values_list("submitted_at", flat=True).first()
        admins = RoleAssignment.objects.filter(
            group__role_group__key__in=area_admin_roles(), is_active=True, user__is_active=True
        )
        recent = [audit_entry(row, describer) for row in AuditLog.objects.order_by("-created_at")[:6]]

        return Response(
            {
                "geo": {
                    "districts": District.objects.count(),
                    "mandals": Mandal.objects.count(),
                    "villages": Village.objects.count(),
                },
                "kpis": {
                    "total_posts": posts.count(),
                    "pending": pending.count(),
                    "pending_districts": sum(1 for row in pending_by_district if row["pending"]),
                    "oldest_pending_hours": hours_since(oldest_pending, now),
                    "escalated": pending.filter(escalated_at__isnull=False).count(),
                    "published_today": published_today,
                    "active_admins": admins.values("user_id").distinct().count(),
                    "active_users": User.objects.filter(is_active=True).count(),
                    "blocked_users": User.objects.filter(is_active=False).count(),
                    "live_villages": Village.objects.active().count(),
                    "inactive_villages": Village.objects.count() - Village.objects.active().count(),
                },
                "escalation_hours": escalation_hours(),
                "posts_per_day": posts_per_day,
                "pending_by_district": pending_by_district,
                "recent_activity": recent,
            }
        )


# --- Reports ---------------------------------------------------------------------------


def window_posts(days: int):
    since = timezone.now() - timedelta(days=days)
    return Post.objects.exclude(status=PostStatus.DRAFT).filter(created_at__gte=since)


def report_summary(days: int) -> dict:
    return posts_counts(window_posts(days))


def report_posts_by_area(days: int) -> list[dict]:
    posts = window_posts(days)
    describer = AreaDescriber()
    rows = []
    for district in District.objects.order_by("name_en"):
        ref = AreaRef(AreaLevel.DISTRICT, district.id)
        # A post for several areas counts once in each district and mandal it reaches.
        row = {"district": describer.describe(ref), **posts_counts(posts.filter(targets_within(ref)))}
        mandals = []
        for mandal in Mandal.objects.filter(district=district).order_by("name_en"):
            mref = AreaRef(AreaLevel.MANDAL, mandal.id)
            counts = posts_counts(posts.filter(targets_within(mref)))
            if counts["total"]:
                mandals.append({"mandal": describer.describe(mref), **counts})
        row["mandals"] = mandals
        rows.append(row)
    return rows


def decision_hours(decisions) -> list[tuple]:
    """(actor_id, actor_role, hours) for each decision, hours since the post's latest (re)submission.

    Decisions with no submission before them are left out. Shared by review-time and admin-activity,
    so both reports measure the same thing.
    """
    rows = list(decisions.values_list("post_id", "actor_id", "actor_role", "created_at"))
    submissions = defaultdict(list)
    for post_id, at in (
        ReviewAction.objects.filter(
            post_id__in={row[0] for row in rows}, action__in=[ReviewAction.Action.SUBMIT, ReviewAction.Action.RESUBMIT]
        ).values_list("post_id", "created_at")
    ):
        submissions[post_id].append(at)
    result = []
    for post_id, actor_id, role, at in rows:
        earlier = [s for s in submissions.get(post_id, []) if s <= at]
        if earlier:
            result.append((actor_id, role, (at - max(earlier)).total_seconds() / 3600))
    return result


def average(total: float, count: int) -> float | None:
    return round(total / count, 1) if count else None


def report_review_time(days: int) -> list[dict]:
    """Average hours from (re)submission to the first decision, per reviewer level."""
    since = timezone.now() - timedelta(days=days)
    decisions = ReviewAction.objects.filter(action__in=DECISIONS, created_at__gte=since).exclude(actor_role="")
    totals = defaultdict(lambda: [0.0, 0])
    levels = level_of_roles()
    for _actor_id, role, hours in decision_hours(decisions):
        level = levels.get(role)
        if level is None:
            continue
        totals[level][0] += hours
        totals[level][1] += 1
    hours = escalation_hours()
    return [
        {
            "level": level,
            "avg_hours": average(*totals[level]),
            "reviews": totals[level][1],
            "escalation_hours": hours,
        }
        for level in (AreaLevel.VILLAGE, AreaLevel.MANDAL, AreaLevel.DISTRICT, AreaLevel.STATE)
    ]


def report_admin_activity(days: int) -> list[dict]:
    since = timezone.now() - timedelta(days=days)
    describer = AreaDescriber()
    assignments = (
        RoleAssignment.objects.filter(group__role_group__key__in=area_admin_roles())
        .select_related("user", "group__role_group")
        .order_by("user__name", "group__role_group__rank", "assigned_at")
    )
    stats = defaultdict(lambda: {"reviewed": 0, "published": 0, "recheck": 0, "discarded": 0, "hours": []})
    actions = ReviewAction.objects.filter(action__in=DECISIONS, created_at__gte=since, actor__isnull=False)
    for actor_id, action in actions.values_list("actor_id", "action"):
        entry = stats[actor_id]
        entry["reviewed"] += 1
        key = {"PUBLISH": "published", "RECHECK": "recheck", "DISCARD": "discarded"}[action]
        entry[key] += 1
    for actor_id, _role, hours in decision_hours(actions):
        stats[actor_id]["hours"].append(hours)
    seen = set()
    rows = []
    for assignment in assignments:
        user = assignment.user
        if user.id in seen:
            continue
        seen.add(user.id)
        entry = stats[user.id]
        rows.append(
            {
                "user": {"id": str(user.id), "name": user.name, "role": assignment.group.role_group.key},
                "area": describer.describe(assignment.area),
                "reviewed": entry["reviewed"],
                "published": entry["published"],
                "recheck": entry["recheck"],
                "discarded": entry["discarded"],
                "avg_review_hours": average(sum(entry["hours"]), len(entry["hours"])),
                "last_active_at": user.last_login,
            }
        )
    rows.sort(key=lambda row: (-row["reviewed"], row["user"]["name"]))
    return rows


REPORTS = {
    "summary": report_summary,
    "posts-by-area": report_posts_by_area,
    "review-time": report_review_time,
    "admin-activity": report_admin_activity,
}


class ReportView(SuperAdminView):
    report = ""

    @extend_schema(summary="Super Admin report")
    def get(self, request):
        days = days_param(request)
        data = REPORTS[self.report](days)
        if self.report == "admin-activity":
            paginator = PageNumberPagination()
            page = paginator.paginate_queryset(data, request, view=self)
            response = paginator.get_paginated_response(page)
            response.data["days"] = days
            return response
        return Response(data if isinstance(data, list) else {**data, "days": days})


def csv_rows(report: str, data) -> tuple[list[str], list[list]]:
    if report == "summary":
        return ["Total", "Published", "Pending", "Rejected"], [[data["total"], data["published"], data["pending"], data["rejected"]]]
    if report == "posts-by-area":
        rows = []
        for district in data:
            rows.append([district["district"]["name"], "", district["total"], district["published"], district["pending"], district["rejected"]])
            for mandal in district["mandals"]:
                rows.append([district["district"]["name"], mandal["mandal"]["name"], mandal["total"], mandal["published"], mandal["pending"], mandal["rejected"]])
        return ["District", "Mandal", "Total", "Published", "Pending", "Rejected"], rows
    if report == "review-time":
        return ["Level", "Average hours", "Reviews", "Escalation hours"], [
            [row["level"], row["avg_hours"] if row["avg_hours"] is not None else "", row["reviews"], row["escalation_hours"]] for row in data
        ]
    return ["Name", "Role", "Area", "Reviewed", "Published", "Recheck", "Discarded", "Average review hours", "Last active"], [
        [
            row["user"]["name"], row["user"]["role"], (row["area"] or {}).get("name", ""),
            row["reviewed"], row["published"], row["recheck"], row["discarded"],
            row["avg_review_hours"] if row["avg_review_hours"] is not None else "",
            timezone.localtime(row["last_active_at"]).strftime("%Y-%m-%d %H:%M") if row["last_active_at"] else "",
        ]
        for row in data
    ]


class CSVRenderer(BaseRenderer):
    """Lets DRF accept ?format=csv. The view builds the CSV itself; errors still go out as JSON."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, str)):
            return data
        return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


class ReportExportView(SuperAdminView):
    renderer_classes = [JSONRenderer, CSVRenderer]

    def handle_exception(self, exc):
        # Errors always go out as JSON, even when ?format=csv picked the CSV renderer.
        response = super().handle_exception(exc)
        # finalize_response copies the request's renderer onto the response.
        self.request.accepted_renderer = response.accepted_renderer = JSONRenderer()
        self.request.accepted_media_type = response.accepted_media_type = JSONRenderer.media_type
        return response

    @extend_schema(summary="Download a report as CSV", responses={(200, "text/csv"): bytes})
    def get(self, request, report):
        if report not in REPORTS:
            raise ApiError("not_found", _("No such report."), status=404)
        if request.query_params.get("format", "csv") != "csv":
            raise ApiError("validation_error", _("Only CSV export is available."), fields={"format": [_("Use csv.")]})
        days = days_param(request)
        header, rows = csv_rows(report, REPORTS[report](days))
        buffer = io.StringIO()
        buffer.write("﻿")  # BOM: Excel then reads Telugu correctly
        writer = safe_writer(buffer)  # names and titles never run as spreadsheet formulas
        writer.writerow(header)
        writer.writerows(rows)
        record(
            "report.exported",
            actor=request.user,
            target_label=f"{report} ({days} days)",
            via=client_via(request),
            data={"report": report, "days": days, "format": "csv"},
        )
        response = HttpResponse(buffer.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8")
        stamp = timezone.localtime().strftime("%Y%m%d")
        response["Content-Disposition"] = f'attachment; filename="grama-{report}-{stamp}.csv"'
        return response

# --- Telugu name suggestions -----------------------------------------------------------------


class HasAnyRole(BasePermission):
    """Any official role anywhere (every role uses the name forms); an account without one gets 403."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False  # 401
        if not authz.grants(request.user):
            raise permission_denied()
        return True


class TransliterateThrottle(UserRateThrottle):
    scope = "transliterate"


class TransliterateQuery(serializers.Serializer):
    text = serializers.CharField(max_length=MAX_LENGTH, trim_whitespace=True)


class TransliterateView(APIView):
    """English name -> Telugu spelling, for the Telugu name boxes (docs/api-contract.md, section 19).

    Only a suggestion: "" when nothing is found, the provider fails or the feature is off, never 5xx.
    """

    permission_classes = [HasAnyRole]
    throttle_classes = [TransliterateThrottle]

    @extend_schema(
        summary="Telugu spelling of an English name (a suggestion for the Telugu name box)",
        parameters=[OpenApiParameter("text", OpenApiTypes.STR, required=True, description="1-150 characters")],
        responses=inline_serializer("Transliteration", {"te": serializers.CharField()}),
    )
    def get(self, request):
        query = TransliterateQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response({"te": to_telugu(query.validated_data["text"])})

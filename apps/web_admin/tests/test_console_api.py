"""Console API: geography writes, CSV import, tree, dashboard, reports. Super Admin only."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.geography.models import District, Mandal, Village
from apps.posts.models import Category, Post, PostStatus, ReviewAction

pytestmark = pytest.mark.django_db

NOT_SUPER = ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"]
ENDPOINTS = [
    ("get", "/api/v1/admin/dashboard/"),
    ("get", "/api/v1/admin/reports/summary/"),
    ("get", "/api/v1/admin/reports/posts-by-area/"),
    ("get", "/api/v1/admin/reports/review-time/"),
    ("get", "/api/v1/admin/reports/admin-activity/"),
    ("get", "/api/v1/admin/reports/summary/export/?format=csv"),
    ("get", "/api/v1/geo/tree/"),
    ("post", "/api/v1/geo/districts/"),
    ("post", "/api/v1/geo/villages/import/"),
]


@pytest.mark.parametrize("name", NOT_SUPER)
@pytest.mark.parametrize("method, url", ENDPOINTS)
def test_only_super_admins(api, world, name, method, url):
    response = getattr(api(world.users[name]), method)(url, {}, format="json") if method == "post" else api(world.users[name]).get(url)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.parametrize("method, url", ENDPOINTS)
def test_needs_login(api, world, method, url):
    response = getattr(api(), method)(url)
    assert response.status_code == 401


# --- Geography writes ----------------------------------------------------------------


def test_create_and_edit_areas(api, world):
    client = api(world.users["super"])
    district = client.post(
        "/api/v1/geo/districts/", {"state_id": world.state.id, "name_en": "Bapatla", "name_te": "బాపట్ల"}, format="json"
    )
    assert district.status_code == 201
    assert district.json()["name_te"] == "బాపట్ల"
    mandal = client.post(
        "/api/v1/geo/mandals/", {"district_id": district.json()["id"], "name_en": "Ponnur"}, format="json"
    )
    assert mandal.status_code == 201
    assert mandal.json()["name_te"] == "Ponnur"  # Telugu copies English when missing
    village = client.post(
        "/api/v1/geo/villages/",
        {"mandal_id": mandal.json()["id"], "name_en": "Chebrolu", "name_te": "చేబ్రోలు",
         "latitude": "16.1960", "longitude": "80.5250", "pin_code": "522212"},
        format="json",
    )
    assert village.status_code == 201
    assert village.json()["latitude"] == "16.196000"

    edit = client.patch(f"/api/v1/geo/villages/{village.json()['id']}/", {"pin_code": "522213"}, format="json")
    assert edit.status_code == 200
    assert edit.json()["pin_code"] == "522213"
    assert AuditLog.objects.filter(action="area.created").count() == 3
    assert AuditLog.objects.filter(action="area.updated").count() == 1


def test_duplicate_names_among_siblings(api, world):
    client = api(world.users["super"])
    response = client.post(
        "/api/v1/geo/mandals/", {"district_id": world.areas["guntur"].id, "name_en": " TENALI "}, format="json"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate"
    elsewhere = client.post(
        "/api/v1/geo/mandals/", {"district_id": world.areas["krishna"].id, "name_en": "Tenali"}, format="json"
    )
    assert elsewhere.status_code == 201


def test_village_needs_coordinates_and_a_parent(api, world):
    client = api(world.users["super"])
    missing = client.post("/api/v1/geo/villages/", {"mandal_id": world.areas["tenali"].id, "name_en": "X"}, format="json")
    assert missing.status_code == 400
    assert {"latitude", "longitude"} <= set(missing.json()["error"]["fields"])
    orphan = client.post(
        "/api/v1/geo/villages/", {"mandal_id": 999999, "name_en": "X", "latitude": "16", "longitude": "80"}, format="json"
    )
    assert orphan.status_code == 400
    assert "mandal_id" in orphan.json()["error"]["fields"]


def test_deactivate_and_reactivate_are_audited(api, world):
    client = api(world.users["super"])
    tenali = world.areas["tenali"]
    off = client.patch(f"/api/v1/geo/mandals/{tenali.id}/", {"is_active": False}, format="json")
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert api().get(f"/api/v1/geo/villages/?mandal={tenali.id}").json() == []
    client.patch(f"/api/v1/geo/mandals/{tenali.id}/", {"is_active": True}, format="json")
    actions = list(AuditLog.objects.filter(action__startswith="area.").order_by("created_at").values_list("action", flat=True))
    assert actions == ["area.deactivated", "area.reactivated"]


def test_public_reads_still_work_on_shared_urls(api, world):
    assert api().get(f"/api/v1/geo/districts/?state={world.state.id}").status_code == 200
    assert api().get(f"/api/v1/geo/villages/{world.areas['kaza'].id}/").status_code == 200


def test_tree_and_admin_villages_include_inactive(api, world):
    Village.objects.filter(pk=world.areas["kaza"].pk).update(is_active=False)
    client = api(world.users["super"])
    [state] = client.get("/api/v1/geo/tree/").json()
    guntur = next(d for d in state["districts"] if d["name_en"] == "Guntur")
    assert guntur["mandal_count"] == 2 and guntur["village_count"] == 4
    villages = client.get(f"/api/v1/geo/admin/villages/?mandal={world.areas['mangalagiri'].id}").json()
    assert {v["name_en"]: v["is_active"] for v in villages} == {"Chinakakani": True, "Kaza": False}


# --- CSV import -------------------------------------------------------------------------

CSV = (
    "state,district,mandal,village,village_te,latitude,longitude\n"
    "Andhra Pradesh,Guntur,Tenali,Pedaravuru,పెదరావూరు,16.2009,80.5703\n"
    "Andhra Pradesh,Guntur,Tenali,Kolakaluru,,,\n"
)


def upload(client, text, dry_run=False):
    url = "/api/v1/geo/villages/import/" + ("?dry_run=1" if dry_run else "")
    return client.post(url, {"file": SimpleUploadedFile("v.csv", text.encode("utf-8"), "text/csv")}, format="multipart")


def test_import_dry_run_then_real(api, world):
    client = api(world.users["super"])
    before = Village.objects.count()
    dry = upload(client, CSV, dry_run=True)
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    assert dry.json()["counts"]["village"] == {"created": 1, "updated": 0, "unchanged": 1}
    assert Village.objects.count() == before
    real = upload(client, CSV)
    assert real.status_code == 200
    assert Village.objects.count() == before + 1
    assert AuditLog.objects.filter(action="area.imported").count() == 1


def test_import_errors_change_nothing(api, world):
    client = api(world.users["super"])
    before = Village.objects.count()
    response = upload(client, CSV + "Andhra Pradesh,Guntur,Tenali,New,,95,80\n")
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any("out of range" in error for error in body["report"]["errors"])
    assert Village.objects.count() == before


def test_import_needs_a_utf8_file(api, world):
    client = api(world.users["super"])
    assert client.post("/api/v1/geo/villages/import/", {}, format="multipart").status_code == 400
    bad = client.post(
        "/api/v1/geo/villages/import/",
        {"file": SimpleUploadedFile("v.csv", "village\nక".encode("utf-16"), "text/csv")},
        format="multipart",
    )
    assert bad.status_code == 400


# --- Dashboard and reports ------------------------------------------------------------------


def make_post(world, author, area, status, *, hours_ago=1, published=False, escalated=False):
    now = timezone.now()
    ref = world.ref(area)
    author_user = world.users[author]
    assignment = author_user.role_assignments.get()
    post = Post.objects.create(
        author=author_user,
        posted_as_role=assignment.role,
        posted_as_level=assignment.area_level,
        posted_as_area_id=assignment.area_id,
        category=Category.objects.get(key="announce"),
        title="పరీక్ష",
        body="body",
        target_level=ref.level,
        target_area_id=ref.id,
        status=status,
        submitted_at=now - timedelta(hours=hours_ago),
        published_at=now - timedelta(hours=hours_ago - 0.5) if published else None,
        escalated_at=now if escalated else None,
    )
    return post


def test_dashboard_numbers(api, world):
    make_post(world, "pm_kolakaluru", "kolakaluru", PostStatus.PENDING, hours_ago=26, escalated=True)
    make_post(world, "pm_kaza", "kaza", PostStatus.PENDING, hours_ago=3)
    make_post(world, "pm_nidamanuru", "nidamanuru", PostStatus.PENDING, hours_ago=25)
    make_post(world, "va_kolakaluru", "kolakaluru", PostStatus.PUBLISHED, hours_ago=1, published=True)
    make_post(world, "pm_nandivelugu", "nandivelugu", PostStatus.DISCARDED)
    Post.objects.create(
        author=world.users["pm_kaza"], posted_as_role="PANCHAYAT_MEMBER", posted_as_level="VILLAGE",
        posted_as_area_id=world.ref("kaza").id, category=Category.objects.get(key="news"),
        title="draft", body="b", target_level="VILLAGE", target_area_id=world.ref("kaza").id, status=PostStatus.DRAFT,
    )
    User.objects.filter(pk=world.users["pm_kaza"].pk).update(is_active=False)

    data = api(world.users["super"]).get("/api/v1/admin/dashboard/").json()
    kpis = data["kpis"]
    assert kpis["total_posts"] == 5  # drafts excluded
    assert kpis["pending"] == 3
    assert kpis["pending_districts"] == 2
    assert kpis["escalated"] == 1
    assert kpis["published_today"] == 1
    assert kpis["oldest_pending_hours"] == pytest.approx(26, abs=0.2)
    assert kpis["active_admins"] == 9
    assert kpis["blocked_users"] == 1
    assert data["geo"] == {"districts": 2, "mandals": 4, "villages": 8}
    by_district = {row["district"]["id"]: row for row in data["pending_by_district"]}
    assert by_district[world.areas["guntur"].id]["pending"] == 2
    assert by_district[world.areas["guntur"].id]["oldest_pending_hours"] == pytest.approx(26, abs=0.2)
    assert by_district[world.areas["krishna"].id]["pending"] == 1
    assert len(data["posts_per_day"]) == 14
    assert sum(day["submitted"] for day in data["posts_per_day"]) == 5


def test_reports(api, world):
    pending = make_post(world, "pm_kolakaluru", "kolakaluru", PostStatus.PENDING, hours_ago=5)
    published = make_post(world, "pm_kaza", "kaza", PostStatus.PUBLISHED, hours_ago=10, published=True)
    make_post(world, "da_guntur", "guntur", PostStatus.PUBLISHED, published=True)
    make_post(world, "pm_nidamanuru", "nidamanuru", PostStatus.UNPUBLISHED)
    now = timezone.now()
    submit = ReviewAction.objects.create(post=published, action="SUBMIT", from_status="DRAFT", to_status="PENDING", actor=world.users["pm_kaza"], actor_role="PANCHAYAT_MEMBER")
    decide = ReviewAction.objects.create(post=published, action="PUBLISH", from_status="PENDING", to_status="PUBLISHED", actor=world.users["va_kaza"], actor_role="VILLAGE_ADMIN")
    ReviewAction.objects.filter(pk=submit.pk).update(created_at=now - timedelta(hours=10))
    ReviewAction.objects.filter(pk=decide.pk).update(created_at=now - timedelta(hours=6))
    assert pending.status == PostStatus.PENDING

    client = api(world.users["super"])
    assert client.get("/api/v1/admin/reports/summary/").json() == {
        "total": 4, "published": 2, "pending": 1, "rejected": 1, "days": 30,
    }
    areas = client.get("/api/v1/admin/reports/posts-by-area/").json()
    guntur = next(row for row in areas if row["district"]["id"] == world.areas["guntur"].id)
    assert (guntur["total"], guntur["published"], guntur["pending"]) == (3, 2, 1)
    assert {m["mandal"]["id"] for m in guntur["mandals"]} == {world.areas["tenali"].id, world.areas["mangalagiri"].id}

    review = {row["level"]: row for row in client.get("/api/v1/admin/reports/review-time/").json()}
    assert review["VILLAGE"] == {"level": "VILLAGE", "avg_hours": 4.0, "reviews": 1, "escalation_hours": 24}

    activity = client.get("/api/v1/admin/reports/admin-activity/?page_size=50").json()
    top = activity["results"][0]
    assert top["user"]["name"] == "va_kaza" and top["reviewed"] == 1 and top["published"] == 1
    assert activity["count"] == 9


def log_action(world, post, action, actor, role, hours_ago):
    row = ReviewAction.objects.create(
        post=post, action=action, from_status="", to_status="", actor=world.users[actor], actor_role=role
    )
    ReviewAction.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))


@pytest.fixture
def reviewed(world):
    """va_kaza decides three times: 4 h, 2 h and 1 h after the latest (re)submission.

    ma_tenali's only decision is 40 days old, outside the default 30-day window.
    """
    first = make_post(world, "pm_kaza", "kaza", PostStatus.PUBLISHED, hours_ago=10, published=True)
    log_action(world, first, "SUBMIT", "pm_kaza", "PANCHAYAT_MEMBER", 10)
    log_action(world, first, "PUBLISH", "va_kaza", "VILLAGE_ADMIN", 6)  # 4 h

    second = make_post(world, "pm_kaza", "kaza", PostStatus.PUBLISHED, hours_ago=5, published=True)
    log_action(world, second, "SUBMIT", "pm_kaza", "PANCHAYAT_MEMBER", 30)
    log_action(world, second, "RECHECK", "va_kaza", "VILLAGE_ADMIN", 28)  # 2 h
    log_action(world, second, "RESUBMIT", "pm_kaza", "PANCHAYAT_MEMBER", 5)
    log_action(world, second, "PUBLISH", "va_kaza", "VILLAGE_ADMIN", 4)  # 1 h from the resubmission, not 26 h

    old = make_post(world, "pm_kolakaluru", "kolakaluru", PostStatus.PUBLISHED, hours_ago=41 * 24, published=True)
    log_action(world, old, "SUBMIT", "pm_kolakaluru", "PANCHAYAT_MEMBER", 41 * 24)
    log_action(world, old, "PUBLISH", "ma_tenali", "MANDAL_ADMIN", 40 * 24)


def test_admin_activity_average_review_hours(api, world, reviewed):
    client = api(world.users["super"])
    rows = {row["user"]["name"]: row for row in client.get("/api/v1/admin/reports/admin-activity/?page_size=50").json()["results"]}
    assert rows["va_kaza"]["reviewed"] == 3
    assert rows["va_kaza"]["avg_review_hours"] == 2.3  # (4 + 2 + 1) / 3
    assert rows["ma_tenali"]["reviewed"] == 0
    assert rows["ma_tenali"]["avg_review_hours"] is None  # outside the window
    assert rows["da_guntur"]["avg_review_hours"] is None  # no decisions at all

    wider = client.get("/api/v1/admin/reports/admin-activity/?days=60&page_size=50").json()["results"]
    assert next(row for row in wider if row["user"]["name"] == "ma_tenali")["avg_review_hours"] == 24.0


def test_review_time_and_admin_activity_agree(api, world, reviewed):
    client = api(world.users["super"])
    review = {row["level"]: row for row in client.get("/api/v1/admin/reports/review-time/").json()}
    assert review["VILLAGE"]["avg_hours"] == 2.3 and review["VILLAGE"]["reviews"] == 3
    assert review["MANDAL"]["avg_hours"] is None


def test_admin_activity_csv_has_average_review_hours(api, world, reviewed):
    client = api(world.users["super"])
    response = client.get("/api/v1/admin/reports/admin-activity/export/?format=csv", HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200
    lines = response.content.decode("utf-8").splitlines()
    assert lines[0] == "﻿Name,Role,Area,Reviewed,Published,Recheck,Discarded,Average review hours,Last active"
    assert any(line.startswith("va_kaza,VILLAGE_ADMIN,Kaza,3,2,1,0,2.3,") for line in lines)
    assert any(line.startswith("da_guntur,DISTRICT_ADMIN,Guntur,0,0,0,0,,") for line in lines)


def test_report_csv_export_has_bom_and_is_audited(api, world):
    make_post(world, "pm_kolakaluru", "kolakaluru", PostStatus.PENDING)
    client = api(world.users["super"])
    response = client.get("/api/v1/admin/reports/posts-by-area/export/?format=csv", HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    text = response.content.decode("utf-8")
    assert text.startswith("﻿District,Mandal,Total")
    assert "Guntur,Tenali,1,0,1,0" in text
    telugu = client.get("/api/v1/admin/reports/posts-by-area/export/?format=csv").content.decode("utf-8")
    assert "గుంటూరు,తెనాలి,1,0,1,0" in telugu
    assert AuditLog.objects.filter(action="report.exported").exists()


def test_bad_report_requests(api, world):
    client = api(world.users["super"])
    assert client.get("/api/v1/admin/reports/nope/export/").status_code == 404
    assert client.get("/api/v1/admin/reports/summary/?days=0").status_code == 400
    # DRF answers 404 for a format it has no renderer for.
    assert client.get("/api/v1/admin/reports/summary/export/?format=pdf").status_code == 404


def test_counts_cover_inactive_areas(api, world):
    District.objects.create(state=world.state, name_en="Prakasam", is_active=False)
    Mandal.objects.filter(pk=world.areas["tenali"].pk).update(is_active=False)
    data = api(world.users["super"]).get("/api/v1/admin/dashboard/").json()
    assert data["geo"]["districts"] == 3
    assert data["kpis"]["inactive_villages"] == 2

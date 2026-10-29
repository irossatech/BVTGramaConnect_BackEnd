"""Who sees a post: Everyone, or any districts, mandals and villages, from any poster (owner decision of 2026-10-28).

The map (conftest.MAP): Guntur > Tenali (Kolakaluru, Nandivelugu), Mangalagiri (Kaza, Chinakakani);
Krishna > Gannavaram (Nidamanuru, Kesarapalli), Penamaluru (Poranki, Kanuru).
"""

import importlib
import json
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.citizens.tests.conftest import phone  # noqa: F401  (fixture: registers a citizen phone)
from apps.notifications.models import Notification, NotificationKind
from apps.notifications.push import LocmemPushGateway, topic_for
from apps.posts.models import Post, PostStatus, PostTarget
from apps.posts.services import escalate_overdue

pytestmark = pytest.mark.django_db

POSTS = "/api/v1/posts/"
VILLAGES = ["kolakaluru", "nandivelugu", "kaza", "chinakakani", "nidamanuru", "kesarapalli", "poranki", "kanuru"]


def code(response):
    return response.json()["error"]["code"]


def body(world, *keys, **extra):
    data = {"category": "announce", "language": "te", "title": "గ్రామ సభ", "body": "అక్టోబర్ 20న.",
            "targets": [world.area_json(key) for key in keys]}
    data.update(extra)
    return data


def post_for(api, world, author, *keys, capture=None, **extra):
    client = api(world.users[author])
    if capture is None:
        return client.post(POSTS, body(world, *keys, **extra), format="json")
    with capture(execute=True):
        return client.post(POSTS, body(world, *keys, **extra), format="json")


def feed_ids(world, village):
    rows = APIClient().get(f"/api/v1/feed/?village_id={world.areas[village].id}").json()["results"]
    return {row["id"] for row in rows}


def seen_in(world, post_id):
    return {village for village in VILLAGES if post_id in feed_ids(world, village)}


def notified(kind):
    return {note.user.username for note in Notification.objects.filter(kind=kind)}


# --- One post for many areas ---------------------------------------------------------------------


def test_one_post_for_a_district_and_a_village_elsewhere(api, world):
    response = post_for(api, world, "va_kolakaluru", "krishna", "kaza")
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == PostStatus.PUBLISHED  # an admin's post publishes at once, wherever it goes
    assert [(row["level"], row["name"]) for row in data["targets"]] == [("DISTRICT", "కృష్ణా"), ("VILLAGE", "కాజా")]
    assert data["targets"][1]["path"] == "కాజా · మంగళగిరి · గుంటూరు"
    assert data["target_area"]["id"] == world.areas["krishna"].id  # the first, for older readers
    assert Post.objects.count() == 1 and PostTarget.objects.count() == 2

    # Every village of Krishna, and Kaza; not Kaza's sibling, nor the poster's own village.
    assert seen_in(world, data["id"]) == {"kaza", "nidamanuru", "kesarapalli", "poranki", "kanuru"}
    row = next(r for r in APIClient().get(f"/api/v1/feed/?village_id={world.areas['poranki'].id}").json()["results"])
    assert [t["level"] for t in row["targets"]] == ["DISTRICT", "VILLAGE"]


def test_everyone_reaches_every_village(api, world, super_admin_can_post):
    post_id = post_for(api, world, "pm_kolakaluru", "ap").json()["id"]
    assert Post.objects.get(pk=post_id).status == PostStatus.PENDING  # a member's post still waits
    super_admin_can_post(False)
    with_review = api(world.users["super"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"}, format="json")
    assert with_review.status_code == 200, with_review.json()
    assert seen_in(world, post_id) == set(VILLAGES)


def test_an_old_client_with_one_target_still_works(api, world):
    one = {"category": "announce", "title": "T", "body": "B", "target": world.area_json("tenali")}
    response = api(world.users["ma_tenali"]).post(POSTS, one, format="json")
    assert response.status_code == 201
    assert [(t["level"], t["id"]) for t in response.json()["targets"]] == [("MANDAL", world.areas["tenali"].id)]
    assert PostTarget.objects.get().area_level == "MANDAL"


def test_targets_win_over_target(api, world):
    data = body(world, "kaza", target=world.area_json("tenali"))
    response = api(world.users["ma_tenali"]).post(POSTS, data, format="json")
    assert [t["id"] for t in response.json()["targets"]] == [world.areas["kaza"].id]


# --- What is saved ------------------------------------------------------------------------------


def test_duplicates_and_areas_inside_another_chosen_one_are_dropped(api, world):
    response = post_for(api, world, "da_guntur", "kolakaluru", "tenali", "tenali", "kaza", "nidamanuru", "gannavaram")
    assert response.status_code == 201, response.json()
    kept = [(t["level"], t["id"]) for t in response.json()["targets"]]
    assert kept == [("MANDAL", world.areas["tenali"].id), ("VILLAGE", world.areas["kaza"].id),
                    ("MANDAL", world.areas["gannavaram"].id)]


def test_children_are_never_merged_into_their_parent(api, world):
    response = post_for(api, world, "ma_tenali", "kolakaluru", "nandivelugu")  # all of Tenali, one by one
    assert [t["level"] for t in response.json()["targets"]] == ["VILLAGE", "VILLAGE"]


def test_everyone_with_other_areas_is_everyone(api, world):
    response = post_for(api, world, "pm_kolakaluru", "kaza", "ap")
    assert [t["level"] for t in response.json()["targets"]] == ["STATE"]


@pytest.mark.parametrize("problem", ["unknown", "inactive", "inactive parent", "none"])
def test_bad_areas_are_refused_and_nothing_is_saved(api, world, problem):
    from apps.geography.models import Mandal, Village

    targets = [world.area_json("kaza")]
    if problem == "unknown":
        targets.append({"level": "VILLAGE", "id": 999999})
    elif problem == "inactive":
        Village.objects.filter(pk=world.areas["kesarapalli"].id).update(is_active=False)
        targets.append(world.area_json("kesarapalli"))
    elif problem == "inactive parent":
        Mandal.objects.filter(pk=world.areas["penamaluru"].id).update(is_active=False)
        targets.append(world.area_json("poranki"))
    else:
        targets = []
    response = api(world.users["va_kolakaluru"]).post(POSTS, {**body(world), "targets": targets}, format="json")
    assert response.status_code == 400 and "targets" in response.json()["error"]["fields"], response.json()
    assert not Post.objects.exists()


def test_at_most_post_max_targets(api, world, settings):
    settings.POST_MAX_TARGETS = 3
    response = post_for(api, world, "va_kolakaluru", "kolakaluru", "kaza", "nidamanuru", "poranki")
    assert response.status_code == 400 and "at most 3" in response.json()["error"]["fields"]["targets"][0]
    assert post_for(api, world, "va_kolakaluru", "kolakaluru", "kaza", "nidamanuru").status_code == 201


def test_a_draft_can_change_its_areas(api, world):
    post_id = post_for(api, world, "pm_kolakaluru", "kolakaluru", submit=False).json()["id"]
    targets = [world.area_json("guntur"), world.area_json("nidamanuru")]
    response = api(world.users["pm_kolakaluru"]).patch(f"{POSTS}{post_id}/", {"targets": targets}, format="json")
    assert response.status_code == 200, response.json()
    assert [t["level"] for t in response.json()["targets"]] == ["DISTRICT", "VILLAGE"]
    assert PostTarget.objects.filter(post_id=post_id).count() == 2
    assert Post.objects.get(pk=post_id).target_level == "DISTRICT"


# --- Review: the admins of every chosen area ------------------------------------------------------


def test_a_pending_post_waits_for_the_admins_over_each_area(api, world, django_capture_on_commit_callbacks):
    post_id = post_for(api, world, "pm_kolakaluru", "krishna", "kaza",
                       capture=django_capture_on_commit_callbacks).json()["id"]
    # Over Krishna: its District Admin. Over Kaza: its Village, Mandal and District Admins. Not the
    # poster's own village or mandal: the post is not for them. The Super Admin sees the queue.
    assert notified(NotificationKind.REVIEW_PENDING) == {"da_krishna", "va_kaza", "ma_mangalagiri", "da_guntur"}
    for name in ("da_krishna", "va_kaza", "ma_mangalagiri", "da_guntur", "super"):
        queue = {row["id"] for row in api(world.users[name]).get("/api/v1/review/queue/").json()["results"]}
        assert post_id in queue, name
        actions = api(world.users[name]).get(f"{POSTS}{post_id}/").json()["allowed_actions"]
        assert "publish" in actions, name
    for name in ("ma_tenali", "va_kolakaluru", "ma_gannavaram", "va_nidamanuru"):
        queue = {row["id"] for row in api(world.users[name]).get("/api/v1/review/queue/").json()["results"]}
        assert post_id not in queue, name
    # ma_gannavaram sits inside Krishna but below it: the post is for the whole district, not their mandal.
    assert api(world.users["ma_gannavaram"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"},
                                                 format="json").status_code == 404


def test_the_first_decision_counts_for_the_whole_post(api, world):
    post_id = post_for(api, world, "pm_kolakaluru", "krishna", "kaza").json()["id"]
    first = api(world.users["da_krishna"]).post(f"{POSTS}{post_id}/review/", {"action": "publish"}, format="json")
    assert first.status_code == 200
    second = api(world.users["va_kaza"]).post(f"{POSTS}{post_id}/review/",
                                              {"action": "discard", "comment": "No"}, format="json")
    assert second.status_code == 409 and code(second) == "already_decided"


def test_a_post_to_everyone_alerts_the_super_admin(api, world, django_capture_on_commit_callbacks):
    post_for(api, world, "pm_kolakaluru", "ap", capture=django_capture_on_commit_callbacks)
    assert notified(NotificationKind.REVIEW_PENDING) == {"super"}


# --- Pushes to citizens --------------------------------------------------------------------------


def test_one_job_pushes_to_each_areas_topic(api, world, django_capture_on_commit_callbacks):
    LocmemPushGateway.outbox.clear()
    post_for(api, world, "da_guntur", "krishna", "kaza", "tenali", capture=django_capture_on_commit_callbacks)
    topics = [m.topic for m in LocmemPushGateway.outbox if m.topic]
    expected = [topic_for(*world.area_json(key).values()) for key in ("krishna", "kaza", "tenali")]
    assert topics == expected


# --- Cost stays the same however many areas ---------------------------------------------------------


@pytest.mark.parametrize("author", ["pm_kolakaluru", "da_guntur"], ids=["pending: reviewer alert", "published: push"])
def test_the_queries_do_not_grow_with_the_areas(api, world, django_capture_on_commit_callbacks, author):
    """Villages without Village Admins, so both posts have the same reviewers (Mangalagiri, Gannavaram,
    both District Admins)."""

    def queries(*keys):
        with CaptureQueriesContext(connection) as captured:
            response = post_for(api, world, author, *keys, capture=django_capture_on_commit_callbacks)
        assert response.status_code == 201, response.json()
        return len(captured)

    queries("chinakakani")  # warm the caches (categories, templates)
    assert queries("chinakakani", "kesarapalli") == queries("chinakakani", "kesarapalli", "poranki", "kanuru")


# --- Escalation ------------------------------------------------------------------------------------


def test_escalation_climbs_from_the_lowest_area_and_alerts_above_each(api, world, django_capture_on_commit_callbacks):
    post_id = post_for(api, world, "pm_kolakaluru", "kolakaluru", "krishna").json()["id"]
    Post.objects.filter(pk=post_id).update(submitted_at=timezone.now() - timedelta(hours=25))
    Notification.objects.all().delete()
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue() == 1
    # Kolakaluru's Mandal Admin, and for the district the level above it: the state.
    assert notified(NotificationKind.ESCALATION) == {"ma_tenali", "super"}

    Notification.objects.all().delete()
    with django_capture_on_commit_callbacks(execute=True):
        assert escalate_overdue(timezone.now() + timedelta(hours=25)) == 1
    assert notified(NotificationKind.ESCALATION) == {"da_guntur"}  # Krishna was alerted already


def test_a_single_village_post_escalates_as_before(api, world, django_capture_on_commit_callbacks):
    post_id = post_for(api, world, "pm_kolakaluru", "kolakaluru").json()["id"]
    Post.objects.filter(pk=post_id).update(submitted_at=timezone.now() - timedelta(hours=25))
    with django_capture_on_commit_callbacks(execute=True):
        escalate_overdue()
    assert notified(NotificationKind.ESCALATION) == {"ma_tenali"}


# --- Citizens --------------------------------------------------------------------------------------


def test_a_citizen_chooses_the_areas_in_the_form(world, phone, api):
    citizen = phone("kolakaluru")
    targets = json.dumps([world.area_json("krishna"), world.area_json("kaza")])
    response = citizen.post(targets=targets)
    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["status"] == PostStatus.PENDING
    assert [t["level"] for t in data["targets"]] == ["DISTRICT", "VILLAGE"]
    assert data["area"]["id"] == world.areas["kolakaluru"].id  # where it was sent from
    post = Post.objects.get(pk=data["id"])
    assert (post.posted_as_level, post.posted_as_area_id) == ("VILLAGE", world.areas["kolakaluru"].id)
    queue = api(world.users["da_krishna"]).get("/api/v1/review/queue/").json()["results"]
    assert str(post.id) in {row["id"] for row in queue}


def test_without_targets_a_citizen_posts_to_their_village(world, phone):
    data = phone("kaza").post().json()
    assert [(t["level"], t["id"]) for t in data["targets"]] == [("VILLAGE", world.areas["kaza"].id)]


@pytest.mark.parametrize("value", ["not json", "[]", '{"level": "VILLAGE"}', '[{"level": "TOWN", "id": 1}]'])
def test_a_bad_targets_field_is_refused(world, phone, value):
    response = phone().post(targets=value)
    assert response.status_code == 400 and "targets" in response.json()["error"]["fields"]
    assert not Post.objects.exists()


def test_the_citizen_limits_are_settings(world, phone, settings):
    citizen = phone()
    settings.CITIZEN_CAN_TARGET_STATE = False
    everyone = citizen.post(targets=json.dumps([{"level": "STATE", "id": world.state.id}]))
    assert everyone.status_code == 400 and "everyone" in everyone.json()["error"]["fields"]["targets"][0]
    settings.CITIZEN_MAX_TARGETS = 1
    two = citizen.post(targets=json.dumps([world.area_json("kaza"), world.area_json("poranki")]))
    assert two.status_code == 400
    assert citizen.post(targets=json.dumps([world.area_json("kaza")])).status_code == 201


# --- Lists, reports and the migration ------------------------------------------------------------


def test_area_filters_and_reports_count_each_area_reached(api, world):
    post_for(api, world, "da_guntur", "tenali", "nidamanuru")
    console = api(world.users["super"])
    for key in ("guntur", "krishna", "tenali", "gannavaram"):
        level, area_id = world.area_json(key).values()
        assert console.get(f"{POSTS}?level={level}&area_id={area_id}").json()["count"] == 1, key
    assert console.get(f"{POSTS}?level=MANDAL&area_id={world.areas['penamaluru'].id}").json()["count"] == 0
    assert console.get(f"{POSTS}?target_level=VILLAGE").json()["count"] == 1
    report = {row["district"]["id"]: row["total"]
              for row in console.get("/api/v1/admin/reports/posts-by-area/?days=30").json()}
    assert report == {world.areas["guntur"].id: 1, world.areas["krishna"].id: 1}


def test_the_migration_gives_every_existing_post_its_one_area(world, announce):
    from conftest import make_bare_post

    post = make_bare_post(
        author=world.users["va_kolakaluru"], posted_as_role="VILLAGE_ADMIN", posted_as_level="VILLAGE",
        posted_as_area_id=world.areas["kolakaluru"].id, category=announce, title="old", body="b",
        target_level="MANDAL", target_area_id=world.areas["tenali"].id, status=PostStatus.PUBLISHED,
    )
    PostTarget.objects.all().delete()
    migration = importlib.import_module("apps.posts.migrations.0008_post_targets")
    migration.copy_targets(django_apps, None)
    assert [(row.area_level, row.area_id) for row in PostTarget.objects.filter(post=post)] == [
        ("MANDAL", world.areas["tenali"].id)
    ]

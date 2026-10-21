"""Home page phone picture (showcase) and the console's Contact messages page (owner decision Q16)."""

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.posts.models import PostStatus
from apps.web_public.models import ContactMessage

pytestmark = pytest.mark.django_db

SHOWCASE = "/api/v1/public/showcase/"
INBOX = "/api/v1/admin/contact-messages/"


# --- Showcase ---------------------------------------------------------------------------


def test_showcase_uses_the_chosen_village_feed(world, make_post, api, settings):
    settings.SITE_SHOWCASE_VILLAGE_ID = world.areas["kolakaluru"].id
    make_post("kaza", title="elsewhere", minutes_ago=0)            # not in Kolakaluru's feed
    make_post("kolakaluru", title="newest text", minutes_ago=1)
    make_post("tenali", title="with photo", minutes_ago=2, media=1)
    make_post("guntur", title="third", minutes_ago=3)
    make_post("ap", title="fourth", minutes_ago=4)
    make_post("kolakaluru", title="hidden", status=PostStatus.PENDING)
    response = api().get(SHOWCASE)
    assert response.status_code == 200
    assert "max-age=60" in response["Cache-Control"] and "public" in response["Cache-Control"]
    data = response.json()
    assert data["location"] == {"level": "VILLAGE", "id": world.areas["kolakaluru"].id, "name": "కొలకలూరు"}
    assert data["featured"]["title"] == "with photo"
    assert data["featured"]["thumbnail_url"]
    assert [item["title"] for item in data["items"]] == ["newest text", "third"]
    assert {"event", "is_urgent", "video", "area"} <= set(data["featured"])


def test_showcase_without_photos_features_the_newest_post(world, make_post, api, settings):
    settings.SITE_SHOWCASE_VILLAGE_ID = world.areas["kolakaluru"].id
    make_post("kolakaluru", title="a", minutes_ago=1)
    make_post("kolakaluru", title="b", minutes_ago=2)
    data = api().get(SHOWCASE).json()
    assert data["featured"]["title"] == "a"
    assert [item["title"] for item in data["items"]] == ["b"]


@pytest.mark.parametrize("village", [None, 999999])
def test_showcase_falls_back_to_the_newest_posts_anywhere(world, make_post, api, settings, village):
    settings.SITE_SHOWCASE_VILLAGE_ID = village
    make_post("kaza", title="kaza news", minutes_ago=1, media=1)
    make_post("krishna", title="krishna", minutes_ago=2)
    data = api().get(SHOWCASE, HTTP_ACCEPT_LANGUAGE="en").json()
    assert data["featured"]["title"] == "kaza news"
    assert data["location"] == {"level": "VILLAGE", "id": world.areas["kaza"].id, "name": "Kaza"}
    assert [item["title"] for item in data["items"]] == ["krishna"]


def test_showcase_falls_back_when_the_chosen_village_has_no_posts(world, make_post, api, settings):
    settings.SITE_SHOWCASE_VILLAGE_ID = world.areas["kanuru"].id
    make_post("kaza", title="kaza news")
    data = api().get(SHOWCASE).json()
    assert data["featured"]["title"] == "kaza news"
    assert data["location"]["id"] == world.areas["kaza"].id


def test_showcase_with_no_posts_at_all(world, api, settings):
    settings.SITE_SHOWCASE_VILLAGE_ID = None
    assert api().get(SHOWCASE).json() == {"location": None, "featured": None, "items": []}


def test_showcase_is_public_with_a_stale_token(world, make_post):
    make_post("kolakaluru")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.garbage")
    assert client.get(SHOWCASE).status_code == 200


# --- Contact messages in the console ----------------------------------------------------


def message(name="రవి", mobile="9876543210", village="కొలకలూరు", text="మా గ్రామాన్ని చేర్చండి", handled=False):
    return ContactMessage.objects.create(
        name=name, mobile=mobile, village_text=village, message=text, language="te", handled=handled
    )


@pytest.mark.parametrize("who", ["da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"])
def test_only_super_admins_read_contact_messages(world, api, who):
    item = message()
    response = api(world.users[who]).get(INBOX)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert api(world.users[who]).patch(f"{INBOX}{item.id}/", {"handled": True}, format="json").status_code == 403
    assert not ContactMessage.objects.get(pk=item.pk).handled


def test_anonymous_cannot_read_contact_messages(world, api):
    assert api().get(INBOX).status_code == 401


def test_contact_messages_newest_first_with_telugu_intact(world, api):
    old = message(name="old")
    new = message(name="సీత", text="లాగిన్ కావాలి")
    response = api(world.users["super"]).get(INBOX)
    assert response.status_code == 200
    rows = response.json()["results"]
    assert [row["id"] for row in rows] == [new.id, old.id]
    assert set(rows[0]) == {
        "id", "name", "mobile", "village_text", "message", "language",
        "created_at", "handled", "handled_at", "handled_by",
    }
    assert rows[0]["name"] == "సీత" and rows[0]["message"] == "లాగిన్ కావాలి"
    assert rows[0]["handled"] is False and rows[0]["handled_by"] is None


def test_contact_messages_filter_by_status_and_search(world, api):
    open_one = message(name="రవి", village="కొలకలూరు")
    done = message(name="Sita", mobile="9123456789", village="Kaza", handled=True)
    client = api(world.users["super"])

    def ids(query):
        return [row["id"] for row in client.get(f"{INBOX}?{query}").json()["results"]]

    assert ids("status=new") == [open_one.id]
    assert ids("status=handled") == [done.id]
    assert ids("q=sita") == [done.id]              # any case
    assert ids("q=91234") == [done.id]             # part of the mobile number
    assert ids("q=కొలకలూరు") == [open_one.id]      # Telugu village text
    assert ids("q=kaza&status=new") == []
    bad = client.get(f"{INBOX}?status=closed")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "validation_error"


def test_contact_messages_are_paged(world, api):
    for index in range(25):
        message(name=f"n{index}")
    client = api(world.users["super"])
    data = client.get(INBOX).json()
    assert data["count"] == 25 and len(data["results"]) == 20
    assert len(client.get(f"{INBOX}?page=2").json()["results"]) == 5


def test_marking_handled_and_reopening_is_recorded(world, console):
    item = message()
    client = console(world.users["super"])
    response = client.patch(f"{INBOX}{item.id}/", {"handled": True}, format="json")
    assert response.status_code == 200
    body = response.json()
    assert body["handled"] is True and body["handled_at"]
    assert body["handled_by"] == {"id": str(world.users["super"].id), "name": "super"}
    item.refresh_from_db()
    assert item.handled and item.handled_at and item.handled_by_id == world.users["super"].id

    # Saying it again changes nothing and writes no second audit row.
    client.patch(f"{INBOX}{item.id}/", {"handled": True}, format="json")
    assert AuditLog.objects.filter(action="contact.handled").count() == 1

    reopened = client.patch(f"{INBOX}{item.id}/", {"handled": False}, format="json").json()
    assert reopened["handled"] is False and reopened["handled_at"] is None and reopened["handled_by"] is None
    rows = list(AuditLog.objects.filter(action__startswith="contact.").order_by("created_at"))
    assert [row.action for row in rows] == ["contact.handled", "contact.reopened"]
    assert all(row.actor_id == world.users["super"].id and row.via == "console" for row in rows)
    # The audit row never carries the sender's name or number.
    assert "రవి" not in rows[0].target_label and "9876543210" not in str(rows[0].data)


def test_patch_needs_handled_and_a_real_message(world, api):
    item = message()
    client = api(world.users["super"])
    assert client.patch(f"{INBOX}{item.id}/", {}, format="json").status_code == 400
    missing = client.patch(f"{INBOX}999999/", {"handled": True}, format="json")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

"""Hostile input in every query parameter, and protected fields in request bodies (audit stage S6,
BVTGC-17, security category).

1. Injection: every query parameter of every list in the API schema (filters, search, ordering, paging)
   gets SQL injection and other attack strings. Each answer must be a normal one (no 500, errors in the
   envelope), a search must not find more than it does for a word that matches nothing, and the tables
   must be intact afterwards. The ORM sends parameters separately from the SQL, so these are expected to
   pass; the test keeps it that way if someone adds raw SQL or .extra() later.
2. Mass assignment: fields the server owns (status, author, role, flags, ids) sent in a create or an
   update are ignored or refused, never stored.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.citizens.tests.conftest import register
from apps.posts.models import Post, PostStatus
from apps.web_public.models import ContactMessage
from conftest import client_for

pytestmark = pytest.mark.django_db

PAYLOADS = [
    "' OR '1'='1",
    "1' OR 1=1 --",
    "1; DROP TABLE accounts_user; --",
    "1 UNION SELECT username, password FROM accounts_user --",
    "\" OR \"\"=\"",
    "%' AND 1=1 --",
    "id; SELECT pg_sleep(5)",
    "1 AND SLEEP(5)",
    "-1",
    "${7*7}",
    "{{7*7}}",
    "<script>alert(1)</script>",
    "../../../../etc/passwd",
    "' ; EXEC xp_cmdshell('dir') --",
    "సర్పంచ్' OR '1'='1",
]
SEARCH_PARAMS = {"q", "search"}


def list_operations() -> list[tuple[str, list[str]]]:
    """(path, query parameter names) for every GET without an id in its path, from the API schema."""
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    found = []
    for path, operations in schema["paths"].items():
        operation = operations.get("get")
        if operation is None or "{" in path or not path.startswith("/api/v1/"):
            continue
        names = [p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"]
        if names:
            found.append((path, sorted(set(names))))
    return sorted(found)


LISTS = list_operations()


def test_the_schema_gives_the_lists_and_their_parameters():
    """A guard on the guard: the lists above really come with their filters."""
    assert len(LISTS) >= 25
    params = {name for _path, names in LISTS for name in names}
    assert {"page", "q"} <= params


def base_query(world, path: str) -> dict:
    """The parameters a list needs to answer at all (the village for the feed, and so on)."""
    village = str(world.ref("kolakaluru").id)
    needs = {
        "/api/v1/feed/": {"village_id": village}, "/api/v1/feed/upcoming/": {"village_id": village},
        "/api/v1/feed/urgent/": {"village_id": village}, "/api/v1/ads/": {"village_id": village},
        "/api/v1/contacts/": {"village_id": village}, "/api/v1/geo/villages/": {"mandal": str(world.ref("tenali").id)},
        "/api/v1/geo/mandals/": {"district": str(world.ref("guntur").id)},
        "/api/v1/admin/transliterate/": {"text": "ravi"},
    }
    return dict(needs.get(path, {}))


def client_for_path(world, path: str, phone_token: str) -> APIClient:
    """A client that hands back a crash as a 500 answer instead of raising it, so every parameter is tried."""
    if path.startswith("/api/v1/citizen/"):
        client = APIClient(HTTP_X_CITIZEN_TOKEN=phone_token)
    else:
        client = client_for(world.users["super"])
    client.raise_request_exception = False
    return client


def answer_is_normal(response) -> str | None:
    """None when the answer is a normal one; else what is wrong."""
    if response.status_code >= 500:
        return f"{response.status_code}"
    kind = response.get("Content-Type", "")
    if response.status_code >= 400 and "json" in kind and "error" not in response.json():
        return f"{response.status_code} without the error envelope"
    if b"Traceback" in response.content or b"OperationalError" in response.content or b"ProgrammingError" in response.content:
        return "a database error or traceback in the answer"
    return None


def results_count(response) -> int | None:
    if response.status_code != 200 or "json" not in response.get("Content-Type", ""):
        return None
    body = response.json()
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        return body.get("count", len(body["results"]))
    return None


@pytest.mark.parametrize("path, params", LISTS, ids=[path for path, _ in LISTS])
def test_attack_strings_in_every_query_parameter(world, make_published_posts, path, params):
    phone = register(world).json()["token"]
    client = client_for_path(world, path, phone)
    users_before = User.objects.count()
    problems = []
    for name in params:
        nothing = results_count(client.get(path, {**base_query(world, path), name: "zzqqxxnomatch"}))
        for payload in PAYLOADS:
            response = client.get(path, {**base_query(world, path), name: payload})
            wrong = answer_is_normal(response)
            if wrong:
                problems.append(f"{name}={payload!r}: {wrong}")
            if name in SEARCH_PARAMS and nothing is not None:
                found = results_count(response)
                if found is not None and found > nothing and "'" in payload:
                    problems.append(f"{name}={payload!r}: found {found} rows where a word matching nothing finds {nothing}")
    assert User.objects.count() == users_before
    assert not problems, f"{path}: " + "; ".join(problems[:10])


HUGE = "99999999999999999999999999"  # larger than any database integer

#: Lists whose area filter answers 500 to HUGE instead of 400 (finding BE-TEST-208, Stage 6 of 2026-11-26):
#: the filter passes the number to the database, which cannot hold it (SQLite raises OverflowError,
#: PostgreSQL "integer out of range"). Not fixed in this stage, by the stage's rule; strict, so the
#: test reports it once it is fixed and the mark can go.
BE_TEST_208 = {
    "/api/v1/admin/citizens/blocked/", "/api/v1/geo/admin/villages/", "/api/v1/geo/districts/",
    "/api/v1/geo/mandals/", "/api/v1/geo/villages/", "/api/v1/posts/", "/api/v1/users/",
}


def huge_number_cases():
    from django.db import connection

    # MySQL and MariaDB may compare the number and find nothing instead of failing: strict on SQLite only.
    strict = connection.vendor == "sqlite"
    reason = "BE-TEST-208: an area id too large for the database answers 500, not 400"
    for path, params in LISTS:
        marks = [pytest.mark.xfail(strict=strict, reason=reason)] if path in BE_TEST_208 else []
        yield pytest.param(path, params, marks=marks, id=path)


@pytest.mark.parametrize("path, params", list(huge_number_cases()))
def test_a_number_too_large_for_the_database_in_every_query_parameter(world, path, params):
    phone = register(world).json()["token"]
    client = client_for_path(world, path, phone)
    problems = []
    for name in params:
        wrong = answer_is_normal(client.get(path, {**base_query(world, path), name: HUGE}))
        if wrong:
            problems.append(f"{name}={HUGE}: {wrong}")
    assert not problems, f"{path}: " + "; ".join(problems)


@pytest.fixture
def make_published_posts(world):
    """A few published posts, so a widened search would show."""
    from apps.posts.services import PostInput, create_post
    from apps.posts.models import Category

    news = Category.objects.get(key="news")
    for title in ("గ్రామ సభ", "Water supply", "Road repair"):
        create_post(world.users["va_kolakaluru"], PostInput(category=news, language="te", title=title, body="body",
                                                             target=world.ref("kolakaluru")))
    return Post.objects.filter(status=PostStatus.PUBLISHED).count()


def test_a_search_with_an_injection_finds_nothing_more(world, api, make_published_posts):
    assert make_published_posts >= 3
    feed = "/api/v1/feed/"
    village = world.ref("kolakaluru").id
    every = api().get(feed, {"village_id": village}).json()["results"]
    assert len(every) >= 3
    for payload in ("' OR '1'='1", "%' OR '%'='", "1' OR 1=1 --"):
        found = api().get(feed, {"village_id": village, "q": payload}).json()["results"]
        assert found == [], payload


# --- Mass assignment ------------------------------------------------------------------------------------


def test_a_new_post_cannot_choose_its_status_author_role_or_share_code(world, api):
    member = world.users["pm_kolakaluru"]
    body = {
        "category": "news", "language": "te", "title": "గ్రామ సభ", "body": "అక్టోబర్ 20న.",
        "targets": [world.area_json("kolakaluru")],
        # The server's own fields:
        "status": "PUBLISHED", "author": str(world.users["super"].pk), "author_id": str(world.users["super"].pk),
        "posted_as_role": "SUPER_ADMIN", "share_code": "HACKED1", "escalated_at": "2020-01-01T00:00:00Z",
        "published_at": "2020-01-01T00:00:00Z",
        "id": "00000000-0000-0000-0000-000000000001", "citizen": None,
    }
    response = api(member).post("/api/v1/posts/", body, format="json")
    assert response.status_code == 201, response.json()
    post = Post.objects.get(pk=response.json()["id"])
    assert str(post.pk) != body["id"]
    assert post.author_id == member.pk
    assert post.status in (PostStatus.DRAFT, PostStatus.PENDING)
    assert post.posted_as_role == "PANCHAYAT_MEMBER"
    assert post.share_code in (None, "") and post.escalated_at is None and post.published_at is None


def test_a_panchayat_member_cannot_mark_a_post_urgent_through_the_body(world, api):
    member = world.users["pm_kolakaluru"]
    body = {"category": "news", "language": "te", "title": "T", "body": "B", "targets": [world.area_json("kolakaluru")],
            "is_urgent": True, "urgent": True}
    response = api(member).post("/api/v1/posts/", body, format="json")
    if response.status_code == 201:
        assert not Post.objects.get(pk=response.json()["id"]).is_urgent
    else:
        assert response.status_code in (400, 403)


def test_an_admin_cannot_raise_an_official_through_the_user_update(world, api):
    target = world.users["pm_kolakaluru"]
    version = target.token_version
    response = api(world.users["va_kolakaluru"]).patch(
        f"/api/v1/users/{target.pk}/",
        {"is_superuser": True, "is_staff": True, "token_version": 99, "password": "Hacked-Password-2026!",
         "must_change_password": False, "groups": [1, 2, 3], "user_permissions": [1]},
        format="json",
    )
    assert response.status_code in (200, 400), response.content
    target.refresh_from_db()
    assert not target.is_superuser and not target.is_staff
    assert target.token_version == version and not target.check_password("Hacked-Password-2026!")
    assert not target.user_permissions.exists()


def test_a_citizen_phone_cannot_unblock_itself_or_change_its_token(world):
    phone = register(world).json()
    client = APIClient(HTTP_X_CITIZEN_TOKEN=phone["token"])
    response = client.patch("/api/v1/citizen/me/", {"blocked": False, "blocked_until": None, "token_hash": "x",
                                                    "posts_today": 0, "id": "00000000-0000-0000-0000-000000000001"},
                            format="json")
    assert response.status_code in (200, 400)
    assert client.get("/api/v1/citizen/me/").json()["id"] == phone["device"]["id"]


def test_a_website_message_cannot_arrive_as_handled(world, api):
    body = {"name": "Ravi", "mobile": "9876543210", "village_text": "Kolakaluru", "message": "Hello", "language": "en",
            "handled": True, "handled_at": "2026-01-01T00:00:00Z", "handled_by": str(world.users["super"].pk),
            "handled_note": "done", "created_at": "2020-01-01T00:00:00Z"}
    response = api().post("/api/v1/public/contact-messages/", body, format="json")
    assert response.status_code == 201, response.content
    message = ContactMessage.objects.get(pk=response.json()["id"])
    assert not message.handled and not message.handled_at and message.handled_by_id is None
    assert message.handled_note == "" and message.created_at.year >= 2026

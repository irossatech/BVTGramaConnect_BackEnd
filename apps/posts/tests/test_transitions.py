"""Post state machine, first-decision-wins, reversal, ReviewAction and AuditLog on every change."""

import pytest

from apps.audit.models import AuditLog
from apps.posts import services
from apps.posts.models import Post, PostStatus, ReviewAction

pytestmark = pytest.mark.django_db


def review(api, world, actor, post, action, comment="", **extra):
    body = {"action": action, "comment": comment, **extra}
    return api(world.users[actor]).post(f"/api/v1/posts/{post.id}/review/", body, format="json")


def code(response):
    return response.json()["error"]["code"]


def actions(post):
    return list(ReviewAction.objects.filter(post=post).order_by("created_at").values_list("action", flat=True))


# --- Creating and submitting ---------------------------------------------------------------


def test_panchayat_post_goes_to_review(api, world):
    response = api(world.users["pm_kolakaluru"]).post(
        "/api/v1/posts/",
        {"category": "announce", "language": "te", "title": "తాగునీటి సరఫరా", "body": "రేపు ఉదయం 6 గంటలకు.",
         "target": world.area_json("kolakaluru")},
        format="json",
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "PENDING"
    assert data["category"]["key"] == "announce"
    assert data["target_area"]["path"] == "కొలకలూరు · తెనాలి · గుంటూరు"
    assert data["posted_as"]["role"] == "PANCHAYAT_MEMBER"
    assert data["allowed_actions"] == []
    post = Post.objects.get()
    assert actions(post) == ["SUBMIT"]
    assert set(AuditLog.objects.values_list("action", flat=True)) == {"post.created", "post.submitted"}


@pytest.mark.parametrize("author, target", [("va_kolakaluru", "kolakaluru"), ("ma_tenali", "nandivelugu"), ("da_guntur", "tenali")])
def test_admin_posts_publish_at_once(make_post, author, target):
    post = make_post(author, target)
    assert post.status == PostStatus.PUBLISHED
    assert post.published_at is not None
    assert actions(post) == ["AUTO_PUBLISH"]
    assert AuditLog.objects.filter(action="post.auto_published", target_id=str(post.id)).exists()


def test_admin_posts_wait_for_review_when_auto_publish_is_off(settings, make_post, api, world):
    settings.ADMIN_POSTS_AUTO_PUBLISH = False
    post = make_post("va_kolakaluru", "kolakaluru")
    assert post.status == PostStatus.PENDING
    assert code(review(api, world, "va_kolakaluru", post, "publish")) == "permission_denied"  # no self-review
    assert review(api, world, "ma_tenali", post, "publish").status_code == 200


@pytest.mark.parametrize(
    "author, target, expected",
    [
        ("pm_kolakaluru", "nandivelugu", "out_of_area"),
        ("pm_kolakaluru", "tenali", "out_of_area"),
        ("va_kolakaluru", "nandivelugu", "out_of_area"),
        ("va_kolakaluru", "tenali", "out_of_area"),
        ("ma_tenali", "kaza", "out_of_area"),
        ("da_guntur", "gannavaram", "out_of_area"),
        ("super", "kolakaluru", "permission_denied"),
    ],
)
def test_posting_outside_your_area_is_refused(api, world, author, target, expected):
    response = api(world.users[author]).post(
        "/api/v1/posts/",
        {"category": "news", "title": "T", "body": "B", "target": world.area_json(target)},
        format="json",
    )
    assert response.status_code == 403
    assert code(response) == expected
    assert not Post.objects.exists()


def test_super_admin_posts_only_when_allowed(super_admin_can_post, make_post):
    post = make_post("super", "ap")
    assert post.status == PostStatus.PUBLISHED
    assert post.posted_as_role == "SUPER_ADMIN"


def test_draft_edit_submit_and_delete(api, world, make_post):
    client = api(world.users["pm_kolakaluru"])
    draft = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    assert draft.status == PostStatus.DRAFT
    edited = client.patch(f"/api/v1/posts/{draft.id}/", {"title": "కొత్త శీర్షిక"}, format="json")
    assert edited.status_code == 200 and edited.json()["title"] == "కొత్త శీర్షిక"
    other = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    assert client.delete(f"/api/v1/posts/{other.id}/").status_code == 204
    submitted = client.post(f"/api/v1/posts/{draft.id}/submit/")
    assert submitted.json()["status"] == "PENDING"
    assert code(client.delete(f"/api/v1/posts/{draft.id}/")) == "invalid_transition"
    assert code(client.patch(f"/api/v1/posts/{draft.id}/", {"title": "x"}, format="json")) == "invalid_transition"


def test_drafts_are_private_to_their_author(api, world, make_post):
    draft = make_post("pm_kolakaluru", "kolakaluru", submit=False)
    assert api(world.users["va_kolakaluru"]).get(f"/api/v1/posts/{draft.id}/").status_code == 404


# --- Reviewing ---------------------------------------------------------------------------------------


def test_recheck_then_resubmit_then_publish(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    sent_back = review(api, world, "va_kolakaluru", post, "recheck", "ఫోటో జోడించండి")
    assert sent_back.status_code == 200
    data = sent_back.json()
    assert data["status"] == "RECHECK"
    assert data["last_review"]["action"] == "RECHECK"
    assert data["last_review"]["comment"] == "ఫోటో జోడించండి"
    assert data["last_review"]["actor"]["role"] == "VILLAGE_ADMIN"

    author = api(world.users["pm_kolakaluru"])
    mine = author.get(f"/api/v1/posts/{post.id}/").json()
    assert mine["allowed_actions"] == ["edit", "add_media", "submit"]
    author.patch(f"/api/v1/posts/{post.id}/", {"body": "సవరించిన వివరాలు"}, format="json")
    assert author.post(f"/api/v1/posts/{post.id}/submit/").json()["status"] == "PENDING"

    published = review(api, world, "va_kolakaluru", post, "publish")
    assert published.json()["status"] == "PUBLISHED"
    assert actions(post) == ["SUBMIT", "RECHECK", "RESUBMIT", "PUBLISH"]

    history = author.get(f"/api/v1/posts/{post.id}/history/").json()
    assert [row["action"] for row in history] == ["PUBLISH", "RESUBMIT", "RECHECK", "SUBMIT"]


@pytest.mark.parametrize("action", ["recheck", "discard"])
def test_comment_is_required(api, world, make_post, action):
    post = make_post("pm_kolakaluru", "kolakaluru")
    response = review(api, world, "va_kolakaluru", post, action, "  ")
    assert response.status_code == 400
    assert "comment" in response.json()["error"]["fields"]
    assert Post.objects.get(pk=post.pk).status == PostStatus.PENDING


def test_take_down_needs_a_reason_and_hides_the_post(api, world, make_post):
    post = make_post("va_kolakaluru", "kolakaluru")
    assert review(api, world, "ma_tenali", post, "unpublish").status_code == 400
    response = review(api, world, "ma_tenali", post, "unpublish", "తప్పు సమాచారం")
    assert response.json()["status"] == "UNPUBLISHED"
    assert AuditLog.objects.filter(action="post.unpublished").exists()


@pytest.mark.parametrize(
    "setup, action, expected",
    [
        ("pending", "unpublish", "invalid_transition"),
        ("published", "publish", "already_decided"),
        ("published", "recheck", "already_decided"),
        ("recheck", "discard", "already_decided"),
        ("unpublished", "unpublish", "already_decided"),
        ("discarded", "unpublish", "invalid_transition"),
    ],
)
def test_invalid_moves_answer_409(api, world, make_post, setup, action, expected):
    post = make_post("pm_kolakaluru", "kolakaluru")
    if setup == "published":
        review(api, world, "va_kolakaluru", post, "publish")
    elif setup == "recheck":
        review(api, world, "va_kolakaluru", post, "recheck", "x")
    elif setup == "discarded":
        review(api, world, "va_kolakaluru", post, "discard", "x")
    elif setup == "unpublished":
        review(api, world, "va_kolakaluru", post, "publish")
        review(api, world, "va_kolakaluru", post, "unpublish", "x")
    response = review(api, world, "ma_tenali", post, action, "reason")
    assert response.status_code == 409
    assert code(response) == expected


# --- First decision wins --------------------------------------------------------------------------------


def test_second_reviewer_gets_409_with_who_decided(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    first = review(api, world, "va_kolakaluru", post, "publish")
    second = review(api, world, "ma_tenali", post, "discard", "duplicate")
    assert first.status_code == 200
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "already_decided"
    assert error["decided_by"] == {"id": str(world.users["va_kolakaluru"].id), "name": "va_kolakaluru", "role": "VILLAGE_ADMIN"}
    assert error["action"] == "PUBLISH"
    assert error["post"]["status"] == "PUBLISHED"
    assert error["at"]
    assert Post.objects.get(pk=post.pk).status == PostStatus.PUBLISHED
    assert actions(post) == ["SUBMIT", "PUBLISH"]


def test_two_reviewers_holding_the_same_stale_copy(world, make_post):
    """Both loaded the post while it was PENDING. Only one conditional UPDATE can match."""
    post = make_post("pm_kolakaluru", "kolakaluru")
    copy_a = Post.objects.get(pk=post.pk)
    copy_b = Post.objects.get(pk=post.pk)
    assert copy_a.status == copy_b.status == PostStatus.PENDING

    services.review(world.users["va_kolakaluru"], copy_a, "recheck", "ఫోటో జోడించండి")
    with pytest.raises(services.AlreadyDecided) as raised:
        services.review(world.users["da_guntur"], copy_b, "publish")

    assert raised.value.post.status == PostStatus.RECHECK
    assert raised.value.post.decided_by == world.users["va_kolakaluru"]
    assert Post.objects.get(pk=post.pk).status == PostStatus.RECHECK
    assert ReviewAction.objects.filter(post=post, action__in=["PUBLISH", "RECHECK"]).count() == 1
    assert AuditLog.objects.filter(action__in=["post.published", "post.recheck"]).count() == 1


def test_conditional_update_is_what_decides(world, make_post):
    """The same check straight on the ORM: the second UPDATE changes no row."""
    post = make_post("pm_kolakaluru", "kolakaluru")
    assert Post.objects.filter(id=post.id, status="PENDING").update(status="PUBLISHED") == 1
    assert Post.objects.filter(id=post.id, status="PENDING").update(status="DISCARDED") == 0


# --- Reversal ------------------------------------------------------------------------------------------------


def test_only_a_strictly_higher_level_can_reverse(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api, world, "va_kolakaluru", post, "discard", "తప్పు")

    same_level = review(api, world, "va_kolakaluru", post, "reverse", "మళ్ళీ చూశాను", to_status="PUBLISHED")
    assert code(same_level) == "permission_denied"

    detail = api(world.users["ma_tenali"]).get(f"/api/v1/posts/{post.id}/").json()
    assert "reverse" in detail["allowed_actions"]
    higher = review(api, world, "ma_tenali", post, "reverse", "సరైన సమాచారమే", to_status="PUBLISHED")
    assert higher.status_code == 200
    assert higher.json()["status"] == "PUBLISHED"
    assert higher.json()["decided_by"]["role"] == "MANDAL_ADMIN"

    # Now a Mandal Admin decided: a Village Admin can no longer touch it, a District Admin can.
    assert code(review(api, world, "va_kolakaluru", post, "reverse", "x", to_status="DISCARDED")) == "permission_denied"
    assert review(api, world, "da_guntur", post, "reverse", "x", to_status="DISCARDED").status_code == 200
    assert review(api, world, "super", post, "reverse", "x", to_status="PENDING").json()["status"] == "PENDING"
    assert actions(post)[-3:] == ["REVERSE", "REVERSE", "REVERSE"]
    assert AuditLog.objects.filter(action="post.reversed").count() == 3


def test_reverse_needs_a_comment_and_a_new_status(api, world, make_post):
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api, world, "va_kolakaluru", post, "discard", "x")
    assert review(api, world, "ma_tenali", post, "reverse", "", to_status="PUBLISHED").status_code == 400
    assert review(api, world, "ma_tenali", post, "reverse", "x").status_code == 400
    assert review(api, world, "ma_tenali", post, "reverse", "x", to_status="DISCARDED").status_code == 400


def test_peers_may_reverse_when_first_decision_does_not_win(settings, api, world, make_post):
    settings.REVIEW_FIRST_DECISION_WINS = False
    post = make_post("pm_kolakaluru", "kolakaluru")
    review(api, world, "va_kolakaluru", post, "discard", "x")
    assert review(api, world, "va_kolakaluru", post, "reverse", "x", to_status="PUBLISHED").status_code == 200


def test_post_list_has_status_counts(api, world, make_post):
    make_post("pm_kolakaluru", "kolakaluru")
    make_post("va_kolakaluru", "kolakaluru")
    make_post("pm_kolakaluru", "kolakaluru", submit=False)
    data = api(world.users["ma_tenali"]).get("/api/v1/posts/").json()
    assert data["count"] == 2
    assert data["status_counts"]["ALL"] == 2
    assert data["status_counts"]["PENDING"] == 1
    assert data["status_counts"]["PUBLISHED"] == 1
    pending = api(world.users["ma_tenali"]).get("/api/v1/posts/?status=PENDING").json()
    assert [row["status"] for row in pending["results"]] == ["PENDING"]
    mine = api(world.users["pm_kolakaluru"]).get("/api/v1/posts/?mine=1").json()
    assert mine["count"] == 2  # own pending post and own draft

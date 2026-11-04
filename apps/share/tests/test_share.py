"""Shared links: share codes, share_url, the /p/<code>/ page with its WhatsApp preview, /app/,
assetlinks.json and feed/share/<code>/ (share-links-backend-plan.md, section 8)."""

import importlib
import io
import re
import uuid

import pytest
from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image
from rest_framework.test import APIClient

from apps.posts.models import Category, MediaKind, Post, PostMedia, PostStatus, PostTranslation
from apps.posts.services import PostInput, create_citizen_post, create_post, review
from apps.posts.sharing import ALPHABET, new_share_code
from conftest import make_bare_post

pytestmark = pytest.mark.django_db

BASE = "https://www.gramaconnect.example/"


@pytest.fixture(autouse=True)
def public_site(settings, tmp_path):
    settings.PUBLIC_BASE_URL = BASE
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PLAY_STORE_URL = ""
    settings.APP_DOWNLOAD_URL = ""


def webp(size=(1600, 900), colour=(31, 138, 59)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "WEBP")
    return buffer.getvalue()


def add_media(post, kind=MediaKind.IMAGE, order=0):
    """A stored photo, or a video with its poster frame (PostMedia.image), as the upload code leaves them."""
    image = default_storage.save(f"posts/test/{uuid.uuid4().hex}.webp", ContentFile(webp()))
    thumb = default_storage.save(f"posts/test/{uuid.uuid4().hex}-320.webp", ContentFile(webp((320, 180))))
    video = default_storage.save(f"posts/test/{uuid.uuid4().hex}.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42")) if kind == MediaKind.VIDEO else ""
    return PostMedia.objects.create(
        post=post, kind=kind, image=image, thumbnail=thumb, video=video, width=1600, height=900,
        bytes=1000, order=order, duration_s=20 if kind == MediaKind.VIDEO else None,
    )


def publish(world, *, title="గ్రామ సభ రేపు", body="రేపు ఉదయం 10 గంటలకు పంచాయతీ కార్యాలయంలో.", area="tenali",
            media=None, urgent=False, author="ma_tenali"):
    """An admin's post, published at once through the services, with optional photos or a video."""
    post = create_post(world.users[author], PostInput(
        category=Category.objects.get(key="announce"), language="te", title=title, body=body,
        target=world.ref(area), is_urgent=urgent,
    ), submit=False)
    for order, kind in enumerate(media or []):
        add_media(post, kind, order)
    from apps.posts.services import submit_post

    return submit_post(world.users[author], post)


def page(code, client=None):
    return (client or APIClient()).get(f"/p/{code}/")


def html(response) -> str:
    return response.content.decode("utf-8")


def meta(response, prop) -> str | None:
    found = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)"', html(response))
    return found.group(1) if found else None


# --- 1. share_code and share_url -------------------------------------------------------------------


def test_publishing_gives_an_8_character_random_code(world):
    post = publish(world)
    assert post.status == PostStatus.PUBLISHED
    assert re.fullmatch(r"[A-Za-z0-9]{8}", post.share_code)


def test_a_pending_post_has_no_code_until_it_is_published(world):
    post = create_post(world.users["pm_kolakaluru"], PostInput(
        category=Category.objects.get(key="announce"), language="te", title="రోడ్డు మరమ్మతు", body="వివరాలు.",
        target=world.ref("kolakaluru"),
    ))
    assert post.status == PostStatus.PENDING and post.share_code is None
    published = review(world.users["va_kolakaluru"], post, "publish")
    assert re.fullmatch(r"[A-Za-z0-9]{8}", published.share_code)


def test_the_code_never_changes_after_take_down_and_reverse(world):
    post = publish(world)
    code = post.share_code
    down = review(world.users["da_guntur"], post, "unpublish", "తప్పు సమాచారం")
    assert down.share_code == code
    again = review(world.users["super"], down, "reverse", "సరిచేశాం", to_status=PostStatus.PUBLISHED)
    assert again.status == PostStatus.PUBLISHED and again.share_code == code


def test_share_url_is_in_every_post_shape_the_app_reads(api, world):
    post = publish(world, urgent=True)
    village = world.areas["kolakaluru"].id
    expected = f"{BASE}p/{post.share_code}/"
    client = APIClient()
    assert client.get(f"/api/v1/feed/?village_id={village}").json()["results"][0]["share_url"] == expected
    assert client.get(f"/api/v1/feed/posts/{post.id}/").json()["share_url"] == expected
    assert client.get(f"/api/v1/feed/posts/?ids={post.id}").json()[0]["share_url"] == expected
    assert client.get(f"/api/v1/feed/urgent/?village_id={village}").json()[0]["share_url"] == expected
    assert client.get(f"/api/v1/feed/share/{post.share_code}/").json()["share_url"] == expected
    official = api(world.users["ma_tenali"])
    assert official.get(f"/api/v1/posts/{post.id}/").json()["share_url"] == expected
    assert official.get("/api/v1/posts/").json()["results"][0]["share_url"] == expected


def test_share_url_is_null_unless_published(api, world):
    pending = create_post(world.users["pm_kolakaluru"], PostInput(
        category=Category.objects.get(key="announce"), language="te", title="రోడ్డు", body="వివరాలు.",
        target=world.ref("kolakaluru"),
    ))
    assert api(world.users["pm_kolakaluru"]).get(f"/api/v1/posts/{pending.id}/").json()["share_url"] is None
    post = publish(world)
    down = review(world.users["da_guntur"], post, "unpublish", "తప్పు")
    assert api(world.users["ma_tenali"]).get(f"/api/v1/posts/{down.id}/").json()["share_url"] is None


def test_share_url_is_null_while_the_public_address_is_not_set(settings, world):
    settings.PUBLIC_BASE_URL = ""
    post = publish(world)
    assert APIClient().get(f"/api/v1/feed/posts/{post.id}/").json()["share_url"] is None
    assert APIClient().get("/api/v1/public/site-config/").json()["invite_url"] is None


def test_codes_do_not_clash():
    codes = {new_share_code() for _ in range(10_000)}
    assert len(codes) == 10_000
    assert all(len(code) == 8 and set(code) <= set(ALPHABET) for code in codes)


def test_the_migration_gives_published_posts_a_code_and_leaves_others(world):
    migration = importlib.import_module("apps.posts.migrations.0009_post_share_code")
    target = world.ref("tenali")
    common = dict(author=world.users["ma_tenali"], posted_as_role="MANDAL_ADMIN", posted_as_level=target.level,
                  posted_as_area_id=target.id, category=Category.objects.get(key="announce"),
                  title="t", body="b", target_level=target.level, target_area_id=target.id)
    published = [make_bare_post(status=PostStatus.PUBLISHED, **common) for _ in range(3)]
    pending = make_bare_post(status=PostStatus.PENDING, **common)
    migration.give_published_posts_a_code(django_apps, None)
    codes = [Post.objects.get(pk=p.pk).share_code for p in published]
    assert all(re.fullmatch(r"[A-Za-z0-9]{8}", code) for code in codes) and len(set(codes)) == 3
    assert Post.objects.get(pk=pending.pk).share_code is None


# --- 2-5. The public post page ------------------------------------------------------------------------


def test_photo_post_page_has_the_text_and_the_preview_tags(world):
    post = publish(world, media=[MediaKind.IMAGE, MediaKind.IMAGE])
    response = page(post.share_code)
    assert response.status_code == 200
    text = html(response)
    assert "గ్రామ సభ రేపు" in text and "పంచాయతీ కార్యాలయంలో" in text
    assert meta(response, "og:title") == "గ్రామ సభ రేపు"
    assert meta(response, "og:url") == f"{BASE}p/{post.share_code}/"
    assert meta(response, "og:locale") == "te_IN"
    assert meta(response, "og:type") == "article"
    assert f'<link rel="canonical" href="{BASE}p/{post.share_code}/">' in text
    assert text.count("<img ") == 2 and 'loading="lazy"' in text
    image = meta(response, "og:image")
    assert image == f"{BASE}media/og/{post.share_code}.jpg"
    assert meta(response, "twitter:card") == "summary_large_image"
    assert "తెనాలి" in text  # the area
    assert "/app/" in text  # "Get the app"


def test_the_preview_picture_is_a_1200_by_630_jpeg_under_300_kb(world):
    post = publish(world, media=[MediaKind.IMAGE])
    page(post.share_code)
    name = f"og/{post.share_code}.jpg"
    assert default_storage.exists(name)
    with default_storage.open(name) as handle:
        data = handle.read()
    assert len(data) <= 300_000
    with Image.open(io.BytesIO(data)) as picture:
        assert picture.format == "JPEG" and picture.size == (1200, 630)
    page(post.share_code)  # a second view reuses it
    assert default_storage.listdir("og")[1] == [f"{post.share_code}.jpg"]


def test_post_text_is_escaped(world):
    post = publish(world, title="<script>alert(1)</script> సభ", body='"><img src=x onerror=alert(1)>')
    text = html(page(post.share_code))
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; సభ" in text
    assert "<img src=x" not in text


def test_video_post_page_has_a_player_and_its_poster_as_the_preview(world):
    post = publish(world, media=[MediaKind.VIDEO])
    response = page(post.share_code)
    video = PostMedia.objects.get(post=post)
    text = html(response)
    assert re.search(rf'<video controls preload="metadata" playsinline poster="{re.escape(video.image.url)}" src="{re.escape(video.video.url)}">', text)
    assert meta(response, "og:image") == f"{BASE}media/og/{post.share_code}.jpg"


def test_text_post_has_no_preview_picture_until_the_category_has_one(world):
    response = page(publish(world).share_code)
    assert response.status_code == 200
    assert meta(response, "og:image") is None
    assert meta(response, "twitter:card") == "summary"


def test_the_translation_shows_below_the_post(world):
    post = publish(world)
    PostTranslation.objects.create(post=post, language="en", title="Gram Sabha tomorrow",
                                   body="Tomorrow at 10 AM.", provider="google", source_hash=post.source_hash)
    text = html(page(post.share_code))
    assert "Gram Sabha tomorrow" in text and "Tomorrow at 10 AM." in text


@pytest.mark.parametrize("action, comment", [("unpublish", "తప్పు సమాచారం")])
def test_a_taken_down_post_is_no_longer_available(world, action, comment):
    post = publish(world)
    review(world.users["da_guntur"], post, action, comment)
    response = page(post.share_code)
    assert response.status_code == 404
    assert "This post is no longer available" in html(response)
    assert meta(response, "og:title") is None


def test_discarded_and_pending_posts_have_no_page(world):
    post = create_post(world.users["pm_kolakaluru"], PostInput(
        category=Category.objects.get(key="announce"), language="te", title="t", body="b", target=world.ref("kolakaluru"),
    ))
    Post.objects.filter(pk=post.pk).update(share_code="Pend1ngX")  # even with a code, pending is not public
    assert page("Pend1ngX").status_code == 404
    review(world.users["va_kolakaluru"], Post.objects.get(pk=post.pk), "discard", "నకిలీ")
    assert page("Pend1ngX").status_code == 404


@pytest.mark.parametrize("code", ["NoSuchC0", "short", "a.b", "a" * 11, "ab-cd", "%27%20OR%201=1"])
def test_unknown_or_malformed_codes_answer_the_gone_page(code):
    response = APIClient().get(f"/p/{code}/")
    assert response.status_code == 404
    assert "no longer available" in html(response)


def test_a_post_in_a_switched_off_category_is_not_available(world):
    post = publish(world)
    Category.objects.filter(key="announce").update(is_active=False)
    assert page(post.share_code).status_code == 404


def test_a_citizen_post_shows_citizen_and_nothing_about_the_phone(world):
    from apps.citizens.models import CitizenDevice

    village = world.areas["kolakaluru"]
    phone = CitizenDevice.objects.create(token_hash=uuid.uuid4().hex, platform="android", village_id=village.id,
                                         nonce=uuid.uuid4().hex)
    post = create_citizen_post(phone, PostInput(
        category=Category.objects.get(key="announce"), language="te", title="వీధి దీపం పనిచేయడం లేదు",
        body="మూడు రోజులుగా.", target=world.ref("kolakaluru"),
    ))
    published = review(world.users["va_kolakaluru"], post, "publish")
    text = html(page(published.share_code))
    assert "పౌరుడు" in text  # "Citizen", in the post's language
    assert str(phone.pk) not in text and phone.token_hash not in text


def test_an_officials_post_shows_the_role_and_area_not_the_name(world):
    post = publish(world)
    text = html(page(post.share_code))
    assert "మండల అడ్మిన్, తెనాలి" in text
    assert world.users["ma_tenali"].name not in text


def test_page_headers_cache_publicly_allow_no_scripts_and_set_no_cookies(world):
    response = page(publish(world).share_code)
    assert response["Cache-Control"] == "public, max-age=300"
    policy = response["Content-Security-Policy"]
    assert "default-src 'none'" in policy and "script-src" not in policy
    assert "frame-ancestors 'none'" in policy
    assert not response.cookies
    assert "<script" not in html(response)


def test_the_page_works_without_a_public_address(settings, world):
    settings.PUBLIC_BASE_URL = ""
    post = publish(world, media=[MediaKind.IMAGE])
    response = page(post.share_code)
    assert response.status_code == 200
    assert meta(response, "og:image") == f"http://testserver/media/og/{post.share_code}.jpg"


def test_only_get_and_head(world):
    code = publish(world).share_code
    assert APIClient().post(f"/p/{code}/").status_code == 405
    assert APIClient().head(f"/p/{code}/").status_code == 200


# --- 7. site-config -------------------------------------------------------------------------------------


def test_site_config_has_the_invite_link():
    assert APIClient().get("/api/v1/public/site-config/").json()["invite_url"] == f"{BASE}app/"


# --- 8. /app/ ---------------------------------------------------------------------------------------------


def test_app_goes_to_the_play_store_when_it_is_set(settings):
    settings.PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.bvt.app"
    response = APIClient().get("/app/")
    assert response.status_code == 302
    assert response["Location"] == "https://play.google.com/store/apps/details?id=com.bvt.app"
    with_ref = APIClient().get("/app/?ref=invite")
    assert with_ref["Location"] == "https://play.google.com/store/apps/details?id=com.bvt.app&ref=invite"


def test_app_shows_a_page_until_the_app_is_on_play():
    response = APIClient().get("/app/")
    assert response.status_code == 200
    text = html(response)
    assert "BVT Grama Connect" in text and "Download for Android" not in text


def test_app_page_offers_the_apk_when_set(settings):
    settings.APP_DOWNLOAD_URL = "/downloads/bvt-grama-connect.apk"
    text = html(APIClient().get("/app/"))
    assert 'href="/downloads/bvt-grama-connect.apk"' in text


# --- 9. App Links and the app's share endpoint ------------------------------------------------------------


def test_assetlinks_lists_the_package_and_every_fingerprint(settings):
    settings.ANDROID_CERT_SHA256 = ["AA:" * 31 + "AA", "BB:" * 31 + "BB"]
    response = APIClient().get("/.well-known/assetlinks.json")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    [statement] = response.json()
    assert statement["relation"] == ["delegate_permission/common.handle_all_urls"]
    assert statement["target"] == {
        "namespace": "android_app", "package_name": "com.bvt.app",
        "sha256_cert_fingerprints": ["AA:" * 31 + "AA", "BB:" * 31 + "BB"],
    }


def test_the_default_fingerprint_is_the_upload_key():
    [statement] = APIClient().get("/.well-known/assetlinks.json").json()
    assert statement["target"]["sha256_cert_fingerprints"] == [
        "27:16:13:C6:5C:CD:BE:3A:45:97:E0:54:4E:CD:09:CA:44:91:E0:CB:07:63:B3:5E:6E:D0:42:89:D5:8A:89:79"
    ]


def test_feed_share_returns_the_post_like_feed_posts(world):
    post = publish(world, media=[MediaKind.IMAGE])
    by_code = APIClient().get(f"/api/v1/feed/share/{post.share_code}/")
    by_id = APIClient().get(f"/api/v1/feed/posts/{post.id}/")
    assert by_code.status_code == 200 and by_code.json() == by_id.json()


def test_feed_share_answers_404_once_taken_down(world):
    post = publish(world)
    review(world.users["da_guntur"], post, "unpublish", "తప్పు")
    response = APIClient().get(f"/api/v1/feed/share/{post.share_code}/")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


# --- 10. The website catch-all ------------------------------------------------------------------------------


def test_the_website_still_serves_its_files_and_does_not_swallow_the_links(settings, tmp_path, world):
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_text("<!doctype html><title>site</title>", encoding="utf-8")
    (public / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    settings.SERVE_FILES, settings.WEB_PUBLIC_DIR = True, public
    client = APIClient()
    assert client.get("/").status_code == 200
    assert client.get("/robots.txt").status_code == 200
    code = publish(world).share_code
    assert "గ్రామ సభ రేపు" in html(client.get(f"/p/{code}/"))
    assert client.get(f"/p/{code}")["Location"] == f"/p/{code}/"
    assert client.get("/app/").status_code == 200
    assert client.get("/.well-known/assetlinks.json")["Content-Type"] == "application/json"


# --- Settings ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env, expected", [("https://www.gramaconnect.com", "https://www.gramaconnect.com/"), ("", "")])
def test_public_base_url_gets_its_final_slash(monkeypatch, env, expected):
    import grama.settings.base as base

    monkeypatch.setenv("PUBLIC_BASE_URL", env)
    try:
        assert importlib.reload(base).PUBLIC_BASE_URL == expected
    finally:
        monkeypatch.delenv("PUBLIC_BASE_URL")
        importlib.reload(base)


@pytest.mark.parametrize("name, value", [("PUBLIC_BASE_URL", "www.gramaconnect.com"), ("ANDROID_CERT_SHA256", "27:16:13")])
def test_settings_refuse_bad_values(monkeypatch, name, value):
    from django.core.exceptions import ImproperlyConfigured

    import grama.settings.base as base

    monkeypatch.setenv(name, value)
    try:
        with pytest.raises(ImproperlyConfigured):
            importlib.reload(base)
    finally:
        monkeypatch.delenv(name)
        importlib.reload(base)

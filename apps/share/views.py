"""Shared links (contract section 6, "Sharing").

/p/<code>/                    a published post as a plain page, server-rendered: WhatsApp's and
                              Facebook's preview crawlers do not run JavaScript, and the website is
                              a static export. Not published (any more): 404 "no longer available".
/app/                         the invite link: to Google Play, or a short page until the app is there
/.well-known/assetlinks.json  Android App Links: phones with the app open /p/ links in it

No personal data on these pages: a citizen post shows "Citizen", an official's post their role and
area, never a name, phone or device. Django's templates escape every post text.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_safe

from apps.accounts.models import RoleGroup
from apps.feed.views import published_posts
from apps.geography.areas import AreaDescriber
from apps.posts.models import CITIZEN_ROLE, MediaKind
from apps.posts.serializers import translation_of
from apps.posts.sharing import share_url

from .og import preview_url

CODE = re.compile(r"[A-Za-z0-9]{1,10}")

MONTHS = {
    "te": ["జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జులై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్", "నవంబర్", "డిసెంబర్"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

WORDS = {
    "te": {
        "open": "యాప్‌లో తెరవండి", "get": "యాప్ పొందండి", "shared": "BVT Grama Connect నుండి షేర్ చేయబడింది",
        "translation": "English", "website": "వెబ్‌సైట్", "citizen": "పౌరుడు",
    },
    "en": {
        "open": "Open in the app", "get": "Get the app", "shared": "Shared from BVT Grama Connect",
        "translation": "తెలుగు", "website": "Website", "citizen": "Citizen",
    },
}


def _source(post, language: str, describer: AreaDescriber) -> str:
    """Who posted it, without a name: "Citizen", or the role's label and the poster's area."""
    if post.citizen_id or post.posted_as_role == CITIZEN_ROLE:
        return WORDS[language]["citizen"]
    group = RoleGroup.objects.select_related("group").filter(key=post.posted_as_role).first()
    area = describer.describe(post.posted_as)
    return ", ".join(part for part in (group.label(language) if group else "", area["name"] if area else "") if part)


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _origin(url: str) -> str | None:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def _security_headers(response, max_age: int):
    """Public cache, no scripts at all, pictures and videos only from this site (or the media host)."""
    hosts = " ".join(sorted({origin for origin in (_origin(settings.MEDIA_URL), _origin(settings.STATIC_URL)) if origin}))
    sources = f"'self' {hosts}".strip()
    response["Cache-Control"] = f"public, max-age={max_age}"
    response["Content-Security-Policy"] = (
        f"default-src 'none'; img-src {sources} data:; media-src {sources}; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return response


def _absolute(request, url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(settings.PUBLIC_BASE_URL or request.build_absolute_uri("/"), url)


def _gone(request):
    response = render(request, "share/gone.html", {"website_url": settings.SITE_WEBSITE_URL}, status=404)
    return _security_headers(response, 60)


@require_safe
def post_page(request, code: str):
    post = None
    if CODE.fullmatch(code):
        post = published_posts().prefetch_related("media").filter(share_code=code).first()
    if post is None:
        return _gone(request)

    language = "te" if post.language == "te" else "en"
    media = sorted(post.media.all(), key=lambda m: (m.order, m.created_at))
    video = next((m for m in media if m.kind == MediaKind.VIDEO), None)
    photos = [m for m in media if m.kind == MediaKind.IMAGE]
    first = video or (photos[0] if photos else None)

    describer = AreaDescriber(language)
    area = describer.describe(post.target)
    source = _source(post, language, describer)
    published = timezone.localtime(post.published_at)
    url = share_url(post) or request.build_absolute_uri()

    context = {
        "post": post,
        "language": language,
        "words": WORDS[language],
        "category": post.category.display_name(language),
        "area": area["path"] if area else "",
        "date": f"{published.day} {MONTHS[language][published.month - 1]} {published.year}",
        "source": source,
        "translation": translation_of(post),
        "photos": photos,
        "video": video,
        "url": url,
        "app_url": settings.PUBLIC_BASE_URL + "app/" if settings.PUBLIC_BASE_URL else "/app/",
        "website_url": settings.SITE_WEBSITE_URL,
        "description": _one_line(post.body, 160),
        "og_description": _one_line(post.body, 200),
        "og_image": _absolute(request, preview_url(post, first)),
        "og_locale": "te_IN" if language == "te" else "en_IN",
    }
    return _security_headers(render(request, "share/post.html", context), settings.SHARE_PAGE_MAX_AGE)


@require_safe
def app_page(request):
    """The invite link: Google Play when the app is there, keeping any query (?ref=...), else a short page."""
    if settings.PLAY_STORE_URL:
        target = settings.PLAY_STORE_URL
        query = request.META.get("QUERY_STRING", "")
        if query:
            target += ("&" if "?" in target else "?") + query
        return HttpResponseRedirect(target)
    context = {"website_url": settings.SITE_WEBSITE_URL, "download_url": settings.APP_DOWNLOAD_URL}
    return _security_headers(render(request, "share/app.html", context), settings.SHARE_PAGE_MAX_AGE)


@require_safe
def assetlinks(request):
    """Lets Android open https://<domain>/p/... links straight in the app (App Links)."""
    statements = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": settings.ANDROID_PACKAGE,
            "sha256_cert_fingerprints": settings.ANDROID_CERT_SHA256,
        },
    }]
    response = JsonResponse(statements, safe=False)
    response["Cache-Control"] = "public, max-age=3600"
    return response

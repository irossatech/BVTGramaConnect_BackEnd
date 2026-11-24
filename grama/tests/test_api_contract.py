"""docs/api-contract.md and the server's addresses match, both ways (audit BE-API-002; stage S6, BVTGC-17).

The contract is the single source of truth the clients code against ("Contract first", CLAUDE.md).
- Every address the server answers under /api/v1/ is written in the contract.
- Every address the contract writes as `METHOD /path` exists on the server: under /api/v1/, or at the
  domain root for the few that live there (/p/<code>/, /app/, /.well-known/assetlinks.json,
  /api/schema/). A path ending in `*` stands for every address under it.

The contract writes ids as `{id}` and often leaves out the trailing slash or adds `?query`; the
server writes `<int:pk>`, `<uuid:pk>` or, in DRF routers, `(?P<pk>[^/.]+)`. Both sides are compared
with every parameter as `{}`.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

CONTRACT = Path(settings.BASE_DIR) / "docs" / "api-contract.md"
API = "api/v1/"
PARAM = re.compile(r"\(\?P<[^>]+>[^)]*\)|<[^>]+>|\{[^}]+\}")
LISTED = re.compile(r"`(?:GET|POST|PATCH|PUT|DELETE) /([^`?\s]*)")


def server_routes() -> set[str]:
    """Every route the URLconf knows, without a leading slash, parameters as {}."""

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = str(pattern.pattern).lstrip("^").rstrip("$")
            route = route.replace("\\Z", "").replace("\\/", "/").replace("\\.", ".")
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + route)
            elif isinstance(pattern, URLPattern):
                yield prefix + route

    return {PARAM.sub("{}", route) for route in walk(get_resolver().url_patterns) if "(?P<format>" not in route}


def bare(path: str) -> str:
    return path.strip("/")


def test_every_api_address_is_in_the_contract():
    contract = PARAM.sub("{}", CONTRACT.read_text(encoding="utf-8"))
    missing = []
    for route in sorted(server_routes()):
        if not route.startswith(API):
            continue
        stem = re.escape(bare(route[len(API):]))
        if not re.search(r"[/`]" + stem + r"(?=[/`?\s|,)])", contract):
            missing.append(route)
    assert not missing, f"Not in docs/api-contract.md: {missing}"


def test_every_address_in_the_contract_exists():
    routes = {bare(route) for route in server_routes()}
    contract = CONTRACT.read_text(encoding="utf-8")
    unknown = []
    for listed in sorted({bare(PARAM.sub("{}", m.group(1))) for m in LISTED.finditer(contract)}):
        path = listed[len(API):] if listed.startswith(API) else listed
        if path.endswith("*"):
            prefix = path.rstrip("*").rstrip("/") + "/"
            found = any(route.startswith(API + prefix) or route.startswith(prefix) for route in routes)
        else:
            found = API + path in routes or path in routes
        if not found:
            unknown.append(listed)
    assert not unknown, f"In docs/api-contract.md but not on the server: {unknown}"

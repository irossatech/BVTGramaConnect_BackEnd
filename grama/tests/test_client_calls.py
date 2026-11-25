"""The contract test (BVTGC-97): every API call of the console, the website and the app still exists here.

docs/client-calls.json lists every call the web repo and the mobile repo make, read from their source by
`python manage.py client_calls` (grama/clientcalls.py). Each call must resolve to a URL of this server
that accepts its method. A pull request that removes or renames a route a client calls, or drops a method
from it, fails here and names the client file that uses it. After a client change, run the command on the
clients' current dev and commit the file.
"""

import json
from functools import lru_cache

import pytest

from grama import clientcalls

DOCUMENT = json.loads(clientcalls.OUTPUT.read_text(encoding="utf-8"))
CALLS = DOCUMENT["calls"]


@lru_cache(maxsize=1)
def words():
    return clientcalls.url_words()


@pytest.mark.parametrize("call", CALLS, ids=[f"{c['method']} {c['path']}" for c in CALLS])
def test_every_client_call_exists_on_this_server(call):
    issue = clientcalls.problem(call["path"], call["method"], words())
    users = "; ".join(f"{client}: {', '.join(files)}" for client, files in call["used_by"].items())
    assert issue is None, f"{call['method']} {call['path']}: {issue}. Called by {users}"


def test_the_list_covers_both_clients_and_the_login_calls():
    clients = {client for call in CALLS for client in call["used_by"]}
    assert clients == {"web", "app"}
    paths = {(call["method"], call["path"]) for call in CALLS}
    for must in [("POST", "/api/v1/auth/login/"), ("POST", "/api/v1/auth/refresh/"), ("POST", "/api/v1/auth/session/login/"),
                 ("GET", "/api/v1/me/"), ("GET", "/api/v1/feed/posts/"), ("POST", "/api/v1/citizen/posts/")]:
        assert must in paths, must
    assert len(CALLS) > 100


def test_the_file_is_what_the_command_writes():
    """Sorted and formatted by clientcalls.dumps, so a fresh run gives no noise in the diff."""
    assert clientcalls.OUTPUT.read_text(encoding="utf-8") == clientcalls.dumps(DOCUMENT)


# --- The check catches what it should -------------------------------------------------------------------


def test_a_removed_or_renamed_route_is_reported():
    assert clientcalls.problem("/api/v1/feed/postz/", "GET", words()) == "no such address"
    assert clientcalls.problem("/api/v1/feed/posts/", "GET", words()) is None


def test_a_method_the_route_does_not_take_is_reported():
    assert clientcalls.problem("/api/v1/me/", "DELETE", words()) == "DELETE not allowed"
    assert clientcalls.problem("/api/v1/posts/{}/review/", "GET", words()) == "GET not allowed"
    assert clientcalls.problem("/api/v1/posts/{}/review/", "POST", words()) is None


def test_a_route_dropped_from_the_urls_fails_the_contract(settings):
    """As if a pull request deleted the feed: the calls that use it no longer pass."""
    import sys
    import types

    from django.urls import clear_url_caches, include, path

    from grama import urls as real

    kept = [p for p in real.api_v1 if getattr(getattr(p, "urlconf_module", None), "__name__", None) != "apps.feed.urls"]
    assert len(kept) == len(real.api_v1) - 1, "the feed is one include in grama.urls.api_v1"
    module = types.ModuleType("grama_tests_without_feed")
    module.urlpatterns = [path("api/v1/", include(kept))]
    sys.modules[module.__name__] = module
    settings.ROOT_URLCONF = module.__name__
    clear_url_caches()
    try:
        feed_calls = [c for c in CALLS if c["path"].startswith("/api/v1/feed/")]
        assert feed_calls
        assert all(clientcalls.problem(c["path"], c["method"], []) == "no such address" for c in feed_calls)
        assert clientcalls.problem("/api/v1/me/", "GET", []) is None
    finally:
        del sys.modules[module.__name__]
        clear_url_caches()


# --- Reading the clients' code --------------------------------------------------------------------------


def scan(text, client):
    calls, unchecked = clientcalls.scan_file(text, "f", client)
    return sorted((c.method, c.path) for c in calls), [u.code for u in unchecked]


def test_reading_console_calls():
    text = """
      h.api.get<Me>('/me/');
      h.api.patch('/admin/ads/' + a.id + '/', {is_active: !a.is_active});
      h.api.del(`/users/${u.id}/role-assignments/${r.id}/`);
      h.api.upload('/geo/villages/import/?dry_run=1', fd);
      h.api.upload(path, fd, undefined, f.key ? 'PATCH' : 'POST');
      params.get('q');  new URLSearchParams(x).get("page");
      request<T>("POST", "/auth/session/login/", { body });
      fetch(apiBase + "/auth/csrf/", { method: "GET" });
    """
    calls, unchecked = scan(text, "web")
    assert calls == [
        ("DELETE", "/api/v1/users/{}/role-assignments/{}/"),
        ("GET", "/api/v1/auth/csrf/"),
        ("GET", "/api/v1/me/"),
        ("PATCH", "/api/v1/admin/ads/{}/"),
        ("POST", "/api/v1/auth/session/login/"),
        ("POST", "/api/v1/geo/villages/import/"),
    ]
    assert unchecked == ["upload(path"]


def test_reading_app_calls():
    text = """
      await _api.get('geo/villages/$vid/');
      await _api.post('posts/${r['id']}/review/', {'action': a});
      await _api.post('users/${u.id}/${block ? 'block' : 'unblock'}/');
      await _api.get(_official ? 'posts/${p.uid}/' : 'feed/posts/${p.uid}/');
      await _api.uploadAll('posts/$id/media/', files, {}, method: 'PATCH');
      await _api.get(r['next'] as String);
      final v = prefs.get('x');
      await _send('POST', 'auth/refresh/', body: {'refresh': refresh});
    """
    calls, unchecked = scan(text, "app")
    assert calls == [
        ("GET", "/api/v1/feed/posts/{}/"),
        ("GET", "/api/v1/geo/villages/{}/"),
        ("GET", "/api/v1/posts/{}/"),
        ("PATCH", "/api/v1/posts/{}/media/"),
        ("POST", "/api/v1/auth/refresh/"),
        ("POST", "/api/v1/posts/{}/review/"),
        ("POST", "/api/v1/users/{}/{}/"),
    ]
    assert unchecked == ["get(r['next'] as String"]

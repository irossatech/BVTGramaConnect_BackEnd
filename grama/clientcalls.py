"""Every API call the console, the website and the app make, read from their source code (BVTGC-97).

`python manage.py client_calls` reads the web repo (`src/`, TypeScript) and the mobile repo (`lib/`, Dart)
and writes `docs/client-calls.json`. `grama/tests/test_client_calls.py` then checks every call in that file
against this server's URLs and methods, so a pull request that removes or renames a route a client still
calls fails CI, naming the client file that calls it.

How a call is read: the clients call the API through small helpers with fixed method names
(web `api.get/getBackground/post/patch/del/upload/download("/path/")`, app `_api.get/post/patch/delete/
upload/uploadAll('path/')`). The first argument is read up to its comma: string pieces are kept, anything
else (`+ a.id +`, `${id}`, `$vid`) becomes `{}`. A call whose address does not start with a string (for
example `h.api.patch(path, …)`) cannot be read; it is listed under `unchecked` with its file, so nobody
believes it is covered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

API_PREFIX = "/api/v1/"
OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "client-calls.json"

WEB_METHODS = {"get": "GET", "getBackground": "GET", "post": "POST", "patch": "PATCH", "del": "DELETE",
               "upload": "POST", "download": "GET"}
APP_METHODS = {"get": "GET", "post": "POST", "patch": "PATCH", "delete": "DELETE", "upload": "POST",
               "uploadAll": "POST"}

_WEB_CALL = re.compile(r"\.(" + "|".join(WEB_METHODS) + r")\s*(?:<[^()]*?>)?\s*\(")
_APP_CALL = re.compile(r"\.(" + "|".join(APP_METHODS) + r")\s*\(")
#: The helpers' own low-level calls with the method first: app `_send('POST', 'auth/refresh/', …)`,
#: console `request<T>("POST", "/auth/session/login/", …)`. A variable address here is the helper itself.
_METHOD_FIRST = re.compile(r"\b(?:_send|request|send)\s*(?:<[^()]*?>)?\(\s*(['\"])(GET|POST|PATCH|PUT|DELETE)\1\s*,")
#: The console's CSRF call: `fetch(apiBase + "/auth/csrf/", { method: "GET", … })`.
_FETCH = re.compile(r"\bfetch\(\s*(?:apiBase|base)\s*\+\s*(?=['\"`])")
_PLACEHOLDERS = re.compile(r"(\{\})+")


@dataclass(frozen=True)
class Call:
    client: str  # "web" or "app"
    method: str
    path: str  # "/api/v1/admin/ads/{}/"
    source: str  # file, relative to the client repo


@dataclass(frozen=True)
class Unchecked:
    client: str
    source: str
    code: str


def _arguments(text: str, start: int) -> list[str]:
    """The call's arguments from `start` (just after its opening parenthesis), split at top-level commas."""
    args, depth, piece, i = [], 0, [], start
    while i < len(text):
        ch = text[i]
        if ch in "'\"`":
            end = _skip_string(text, i)
            piece.append(text[i:end])
            i = end
            continue
        if ch in "([{":
            depth += 1
            piece.append(ch)
        elif ch in ")]}":
            if depth == 0:
                args.append("".join(piece).strip())
                return args
            depth -= 1
            piece.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(piece).strip())
            piece = []
        else:
            piece.append(ch)
        i += 1
    return args


def _skip_string(text: str, i: int) -> int:
    """Index just after the string literal that starts at `i` (nested `${…}` included)."""
    return _read_literal(text, i, dart=True)[1]


def _read_literal(text: str, i: int, *, dart: bool) -> tuple[str, int]:
    """The literal starting at `text[i]` (a quote) with every interpolation as `{}`, and the index after it.

    `${…}` may hold quotes and braces of its own (`'posts/${r['id']}/'`, `${block ? 'unblock' : 'block'}`):
    it is skipped as a whole. Dart's `$name` is one value too.
    """
    quote, out, j = text[i], [], i + 1
    interpolates = dart or quote == "`"
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            out.append(text[j:j + 2])
            j += 2
        elif ch == quote:
            return "".join(out), j + 1
        elif interpolates and ch == "$" and text[j + 1:j + 2] == "{":
            depth, k = 1, j + 2
            while k < len(text) and depth:
                if text[k] in "'\"`":
                    k = _skip_string(text, k)
                    continue
                depth += text[k] == "{"
                depth -= text[k] == "}"
                k += 1
            out.append("{}")
            j = k
        elif dart and ch == "$" and re.match(r"[A-Za-z_]", text[j + 1:j + 2]):
            k = j + 1
            while k < len(text) and (text[k].isalnum() or text[k] == "_"):
                k += 1
            out.append("{}")
            j = k
        else:
            out.append(ch)
            j += 1
    return "".join(out), j


def address(expression: str, *, dart: bool) -> str | None:
    """`'/admin/ads/' + a.id + '/'` -> `/admin/ads/{}/`. None when it does not start with a string."""
    out, i, first = [], 0, True
    expression = expression.strip()
    while i < len(expression):
        ch = expression[i]
        if ch.isspace() or ch == "+":
            i += 1
            continue
        if ch in "'\"`":
            literal, i = _read_literal(expression, i, dart=dart)
            out.append(literal)
        else:
            if first:
                return None
            # Anything else up to the next top-level "+" is one value.
            depth = 0
            while i < len(expression) and not (expression[i] == "+" and depth == 0):
                depth += expression[i] in "([{"
                depth -= expression[i] in ")]}"
                i += 1
            out.append("{}")
        first = False
    path = "".join(out).split("?", 1)[0]
    return _PLACEHOLDERS.sub("{}", path)


def _branches(expression: str) -> list[str]:
    """`cond ? 'a/' : 'b/'` -> both choices; anything else as it is."""
    depth, i, question = 0, 0, None
    while i < len(expression):
        ch = expression[i]
        if ch in "'\"`":
            i = _skip_string(expression, i)
            continue
        depth += ch in "([{"
        depth -= ch in ")]}"
        if depth == 0 and ch == "?" and expression[i + 1:i + 2] not in ("?", ".") and expression[i - 1:i] != "?":
            question = i
        elif depth == 0 and ch == ":" and question is not None:
            return [expression[question + 1:i].strip(), expression[i + 1:].strip()]
        i += 1
    return [expression]


def _full(path: str) -> str:
    return API_PREFIX + path.lstrip("/")


def _extra_methods(args: list[str], default: str) -> list[str]:
    """upload/uploadAll may name PATCH or PUT in a later argument (`'PATCH'`, `method: 'PATCH'`)."""
    rest = " ".join(args[1:])
    named = [m for m in ("PATCH", "PUT") if re.search(r"['\"`]" + m + r"['\"`]", rest)]
    if named and not re.search(r"['\"`]POST['\"`]", rest) and "?" not in rest:
        return named
    return [default, *named]


def scan_file(text: str, source: str, client: str) -> tuple[list[Call], list[Unchecked]]:
    dart = client == "app"
    pattern, methods = (_APP_CALL, APP_METHODS) if dart else (_WEB_CALL, WEB_METHODS)
    calls, unchecked = [], []
    for match in pattern.finditer(text):
        name = match.group(1)
        args = _arguments(text, match.end())
        if not args or not args[0]:
            continue
        paths = [address(choice, dart=dart) for choice in _branches(args[0])]
        if None in paths:
            # Only helpers we know take an address: a variable first argument is a real, unreadable call
            # when the receiver is an API object; anything else (a Map's get) is ignored.
            receiver = text[max(0, match.start() - 12):match.start()]
            if re.search(r"(api|_api|Api)$", receiver):
                unchecked.append(Unchecked(client, source, f"{name}({args[0]}"[:80]))
            continue
        verbs = _extra_methods(args, methods[name]) if name in ("upload", "uploadAll") else [methods[name]]
        for path in paths:
            if "/" not in path or (not dart and not path.startswith("/")) or path.startswith(("http", "{}")):
                continue  # not an API address (a header name, a full URL, a Map key)
            calls.extend(Call(client, verb, _full(path), source) for verb in verbs)
    for match in _METHOD_FIRST.finditer(text):
        args = _arguments(text, match.end())
        path = address(args[0], dart=dart) if args and args[0] else None
        if path and "/" in path and not path.startswith(("http", "{}")):
            calls.append(Call(client, match.group(2), _full(path), source))
    for match in _FETCH.finditer(text):
        args = _arguments(text, match.end())
        path = address(args[0], dart=dart) if args else None
        if path and "/" in path:
            named = re.search(r"method:\s*['\"](GET|POST|PATCH|PUT|DELETE)['\"]", args[1] if len(args) > 1 else "")
            calls.append(Call(client, named.group(1) if named else "GET", _full(path), source))
    return calls, unchecked


def scan(web_dir: Path | None, app_dir: Path | None) -> tuple[list[Call], list[Unchecked]]:
    calls, unchecked = [], []
    for client, root, sub, suffixes in (("web", web_dir, "src", (".ts", ".tsx")), ("app", app_dir, "lib", (".dart",))):
        if root is None:
            continue
        for file in sorted((root / sub).rglob("*")):
            if file.suffix not in suffixes or ".test." in file.name or file.name.endswith(".d.ts"):
                continue
            source = file.relative_to(root).as_posix()
            found, skipped = scan_file(file.read_text(encoding="utf-8"), source, client)
            calls += found
            unchecked += skipped
    return calls, unchecked


def as_document(calls: list[Call], unchecked: list[Unchecked]) -> dict:
    grouped: dict[tuple[str, str], dict[str, set[str]]] = {}
    for call in calls:
        grouped.setdefault((call.path, call.method), {}).setdefault(call.client, set()).add(call.source)
    return {
        "about": "Every API call of the web console and website (web) and the mobile app (app), read from their "
                 "source by `python manage.py client_calls` (grama/clientcalls.py, BVTGC-97). "
                 "grama/tests/test_client_calls.py checks each against the server's URLs. {} is any value.",
        "calls": [
            {"method": method, "path": path, "used_by": {client: sorted(files) for client, files in sorted(users.items())}}
            for (path, method), users in sorted(grouped.items())
        ],
        "unchecked": sorted(
            ({"client": u.client, "file": u.source, "code": u.code} for u in set(unchecked)),
            key=lambda u: (u["client"], u["file"], u["code"]),
        ),
    }


def dumps(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


# --- Checking a call against this server -----------------------------------------------------------------

#: Values tried for each `{}`: an id, a UUID, a key, then every fixed word of the server's own URLs
#: (for an address such as `/geo/{}/{}/` built from a variable like "districts").
_SAMPLES = ["1", "00000000-0000-4000-8000-000000000000", "abc"]


def url_words() -> list[str]:
    """Every fixed word in this server's URL patterns (`districts`, `block-citizen`, …)."""
    from django.urls import get_resolver

    words: set[str] = set()

    def walk(patterns):
        for pattern in patterns:
            text = str(pattern.pattern)
            words.update(re.findall(r"[a-z][a-z0-9_-]+", re.sub(r"<[^>]+>|\(\?P<[^>]+>[^)]*\)", "", text)))
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns)

    walk(get_resolver().url_patterns)
    return sorted(words)


def _allows(match, method: str) -> bool:
    func = match.func
    actions = getattr(func, "actions", None)
    if actions is not None:  # a DRF viewset route
        return method.lower() in actions
    view = getattr(func, "cls", None) or getattr(func, "view_class", None)
    if view is None:
        return True  # a plain function view: any method reaches it
    return method.lower() in getattr(view, "http_method_names", []) and hasattr(view, method.lower())


def problem(path: str, method: str, words: list[str] | None = None) -> str | None:
    """None when this server answers `method` at `path`; else what is wrong."""
    from itertools import product

    from django.urls import Resolver404, resolve

    holes = path.count("{}")
    pools = [_SAMPLES] if holes == 0 else [_SAMPLES, _SAMPLES + (words if words is not None else url_words())]
    resolved = None
    for pool in pools:
        for values in product(pool, repeat=holes):
            candidate = path
            for value in values:
                candidate = candidate.replace("{}", value, 1)
            try:
                match = resolve(candidate)
            except Resolver404:
                continue
            resolved = resolved or match
            if _allows(match, method):
                return None
    if resolved is None:
        return "no such address"
    return f"{method} not allowed"

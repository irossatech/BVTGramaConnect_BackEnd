"""The API's shape against a stored snapshot: breaking changes fail (audit stage S6, BVTGC-17).

grama/tests/config/openapi-snapshot.json keeps, for every address and method of the drf-spectacular
schema, the request fields (with their types and which are required) and the success responses' fields
(with their types, one level into lists and pages). A run compares today's schema with it:

- breaking, the test fails: an address or a method gone, a response field gone, a field's type changed,
  a request field that became required, or a new required request field;
- not breaking, the test passes and names them: new addresses, new response fields, new optional
  request fields.

An agreed change (docs/api-contract.md, section 1, "Versions and changes") is recorded by writing the
snapshot again:

    UPDATE_SCHEMA_SNAPSHOT=1 python -m pytest grama/tests/test_schema_snapshot.py   (Linux, macOS)
    $env:UPDATE_SCHEMA_SNAPSHOT=1; python -m pytest grama/tests/test_schema_snapshot.py   (PowerShell)

and committing it in the same pull request as the contract change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parent / "config" / "openapi-snapshot.json"
SUCCESS = ("200", "201", "202", "204")


def current_schema() -> dict:
    from drf_spectacular.generators import SchemaGenerator

    return SchemaGenerator().get_schema(request=None, public=True)


def _type(node: dict, components: dict, depth: int) -> str:
    node = _resolve(node, components)
    if "oneOf" in node or "anyOf" in node or "allOf" in node:
        options = node.get("oneOf") or node.get("anyOf") or node.get("allOf")
        return "|".join(sorted({_type(option, components, depth) for option in options}))
    kind = node.get("type", "object" if "properties" in node else "any")
    if isinstance(kind, list):
        kind = "|".join(sorted(kind))
    if kind == "array":
        return "array<" + _type(node.get("items", {}), components, depth) + ">"
    if node.get("nullable"):
        kind += "?"
    return kind


def _resolve(node: dict, components: dict) -> dict:
    while "$ref" in node:
        node = components[node["$ref"].rsplit("/", 1)[-1]]
    return node


def _fields(node: dict, components: dict, prefix: str = "", depth: int = 0) -> dict[str, str]:
    """Field name -> type, one level into objects in lists ("results[].title")."""
    node = _resolve(node or {}, components)
    if node.get("type") == "array" and depth == 0:
        return _fields(node.get("items", {}), components, prefix + "[].", depth + 1)
    found = {}
    for name, child in (node.get("properties") or {}).items():
        found[prefix + name] = _type(child, components, depth)
        inner = _resolve(child, components)
        if depth == 0 and inner.get("type") == "array":
            found.update(_fields(inner.get("items", {}), components, f"{prefix}{name}[].", depth + 1))
        elif depth == 0 and "properties" in inner:
            found.update(_fields(inner, components, f"{prefix}{name}.", depth + 1))
    return found


def compact(schema: dict) -> dict:
    """{"METHOD /path": {"request": {field: type}, "required": [...], "responses": {status: {field: type}}}}"""
    components = schema.get("components", {}).get("schemas", {})
    out = {}
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            body = (operation.get("requestBody") or {}).get("content", {})
            request = next((body[kind]["schema"] for kind in ("application/json", "multipart/form-data") if kind in body), {})
            request = _resolve(request, components)
            responses = {}
            for status, answer in (operation.get("responses") or {}).items():
                if status in SUCCESS:
                    content = (answer.get("content") or {}).get("application/json", {})
                    responses[status] = _fields(content.get("schema", {}), components)
            out[f"{method.upper()} {path}"] = {
                "request": _fields(request, components) if request else {},
                "required": sorted(request.get("required", [])) if request else [],
                "responses": responses,
            }
    return dict(sorted(out.items()))


def differences(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """(breaking, additive) changes from `old` to `new`, each as one readable line."""
    breaking, additive = [], []
    for operation in sorted(old.keys() - new.keys()):
        breaking.append(f"{operation}: gone")
    for operation in sorted(new.keys() - old.keys()):
        additive.append(f"{operation}: new")
    for operation in sorted(old.keys() & new.keys()):
        before, after = old[operation], new[operation]
        for status, fields in before["responses"].items():
            now = after["responses"].get(status)
            if now is None:
                breaking.append(f"{operation}: answer {status} gone")
                continue
            for field, kind in fields.items():
                if field not in now:
                    breaking.append(f"{operation}: response field {field} gone")
                elif now[field] != kind:
                    breaking.append(f"{operation}: response field {field} changed from {kind} to {now[field]}")
            additive += [f"{operation}: new response field {field}" for field in sorted(now.keys() - fields.keys())]
        for field, kind in before["request"].items():
            if field in after["request"] and after["request"][field] != kind:
                breaking.append(f"{operation}: request field {field} changed from {kind} to {after['request'][field]}")
        for field in sorted(set(after["required"]) - set(before["required"])):
            breaking.append(f"{operation}: request field {field} is now required")
        additive += [f"{operation}: new optional request field {field}"
                     for field in sorted(after["request"].keys() - before["request"].keys()) if field not in after["required"]]
    return breaking, additive


# --- The real schema ------------------------------------------------------------------------------------


def test_the_api_has_no_breaking_change_against_the_snapshot():
    new = compact(current_schema())
    if os.environ.get("UPDATE_SCHEMA_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(new, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("snapshot written; commit grama/tests/config/openapi-snapshot.json")
    assert SNAPSHOT.exists(), "No snapshot yet: write it with UPDATE_SCHEMA_SNAPSHOT=1 (see this file's docstring)"
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    breaking, additive = differences(old, new)
    assert not breaking, (
        "Breaking API changes against grama/tests/config/openapi-snapshot.json. If agreed with the clients "
        "(contract section 1), write the snapshot again (UPDATE_SCHEMA_SNAPSHOT=1):\n  " + "\n  ".join(breaking)
    )
    if additive:
        print("Additive API changes (not breaking; refresh the snapshot when convenient):", *additive, sep="\n  ")


def test_the_snapshot_covers_every_api_address():
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert len(old) >= 150
    assert all(operation.split(" ", 1)[1].startswith(("/api/v1/", "/p/", "/app/", "/.well-known/"))
               or operation.split(" ", 1)[1].startswith("/api/") for operation in old)


# --- The comparison itself, on small made-up schemas ------------------------------------------------------


def schema_with(response_fields: dict, request_fields: dict | None = None, required=(), path="/api/v1/x/") -> dict:
    return {
        "paths": {path: {"post": {
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": request_fields or {}, "required": list(required)}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/X"}}}}},
        }}},
        "components": {"schemas": {"X": {"type": "object", "properties": response_fields}}},
    }


BASE = schema_with({"id": {"type": "integer"}, "title": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}},
                   {"title": {"type": "string"}}, required=["title"])


def test_the_same_schema_has_no_differences():
    assert differences(compact(BASE), compact(BASE)) == ([], [])


@pytest.mark.parametrize("changed, expected", [
    (schema_with({"id": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}},
                 {"title": {"type": "string"}}, ["title"]), "response field title gone"),
    (schema_with({"id": {"type": "string"}, "title": {"type": "string"},
                  "tags": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}},
                 {"title": {"type": "string"}}, ["title"]), "response field id changed from integer to string"),
    (schema_with({"id": {"type": "integer"}, "title": {"type": "string"},
                  "tags": {"type": "array", "items": {"type": "object", "properties": {}}}},
                 {"title": {"type": "string"}}, ["title"]), "response field tags[].name gone"),
    (schema_with({"id": {"type": "integer"}, "title": {"type": "string"},
                  "tags": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}},
                 {"title": {"type": "string"}, "village": {"type": "integer"}}, ["title", "village"]),
     "request field village is now required"),
    (schema_with({"id": {"type": "integer"}}, path="/api/v1/y/"), "POST /api/v1/x/: gone"),
])
def test_breaking_changes_are_caught(changed, expected):
    breaking, _additive = differences(compact(BASE), compact(changed))
    assert any(expected in line for line in breaking), breaking


def test_additions_are_not_breaking():
    grown = schema_with({"id": {"type": "integer"}, "title": {"type": "string"}, "share_url": {"type": "string"},
                         "tags": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}}},
                        {"title": {"type": "string"}, "urgent": {"type": "boolean"}}, ["title"])
    breaking, additive = differences(compact(BASE), compact(grown))
    assert breaking == []
    assert any("new response field share_url" in line for line in additive)
    assert any("new optional request field urgent" in line for line in additive)

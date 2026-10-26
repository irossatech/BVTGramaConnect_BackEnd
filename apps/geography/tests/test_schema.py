"""Every geography endpoint is in the OpenAPI schema (Swagger) with its request body and its auth.

The URLs that share a public GET and a Super Admin write go through
views_admin.dispatch_by_method; drf-spectacular only sees them through the
schema-only classes. Behaviour is covered in test_api.py.
"""

import pytest

pytestmark = pytest.mark.django_db

PUBLIC_GETS = ["/api/v1/geo/districts/", "/api/v1/geo/mandals/", "/api/v1/geo/villages/", "/api/v1/geo/villages/{id}/"]
WRITES = [
    ("post", "/api/v1/geo/districts/", "state_id"),
    ("post", "/api/v1/geo/mandals/", "district_id"),
    ("post", "/api/v1/geo/villages/", "mandal_id"),
    ("patch", "/api/v1/geo/districts/{id}/", "is_active"),
    ("patch", "/api/v1/geo/mandals/{id}/", "is_active"),
    ("patch", "/api/v1/geo/villages/{id}/", "latitude"),
]


@pytest.fixture(scope="module")
def schema(django_db_blocker):
    from drf_spectacular.generators import SchemaGenerator

    with django_db_blocker.unblock():
        return SchemaGenerator().get_schema(request=None, public=True)


def resolve(schema, ref_or_schema):
    ref = ref_or_schema.get("$ref")
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]] if ref else ref_or_schema


@pytest.mark.parametrize("path", PUBLIC_GETS)
def test_public_lists_are_documented_without_a_login(schema, path):
    operation = schema["paths"][path]["get"]
    assert not operation.get("security")  # no padlock: citizens call these without logging in
    assert "200" in operation["responses"]


@pytest.mark.parametrize("method, path, field", WRITES)
def test_super_admin_writes_have_a_body_and_need_a_login(schema, method, path, field):
    operation = schema["paths"][path][method]
    assert {"jwtAuth": []} in operation["security"]
    body = resolve(schema, operation["requestBody"]["content"]["application/json"]["schema"])
    assert field in body["properties"]


def test_console_reads_and_import_are_described(schema):
    tree = schema["paths"]["/api/v1/geo/tree/"]["get"]
    assert tree["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "array"
    village_rows = schema["paths"]["/api/v1/geo/admin/villages/"]["get"]
    assert any(p["name"] == "mandal" and p["required"] for p in village_rows["parameters"])
    upload = schema["paths"]["/api/v1/geo/villages/import/"]["post"]
    assert "file" in upload["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"]
    assert any(p["name"] == "dry_run" for p in upload["parameters"])

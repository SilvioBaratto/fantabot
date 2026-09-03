"""End-to-end app-boot tests (issue #11).

Proves the application object builds, its OpenAPI schema is generated, and the
documentation endpoints are reachable. Uses the shared ``client`` fixture so the
lifespan startup (``init_db``) runs against the overridden test session.
"""

import pytest

from app.main import app


@pytest.mark.e2e
def test_when_app_imported_then_application_object_is_built():
    """when the app is imported, the FastAPI application object is built."""
    assert app is not None
    assert app.title


@pytest.mark.e2e
def test_when_openapi_is_built_then_schema_dict_is_returned():
    """when app.openapi() is called, a schema dict with openapi/paths keys is returned."""
    schema = app.openapi()

    assert isinstance(schema, dict)
    assert "openapi" in schema
    assert "paths" in schema


@pytest.mark.e2e
def test_when_docs_requested_then_200_is_returned(client):
    """when GET /docs is requested, 200 is returned."""
    assert client.get("/docs").status_code == 200


@pytest.mark.e2e
def test_when_openapi_json_requested_then_200_and_schema_is_served(client):
    """when GET /openapi.json is requested, 200 and a schema with openapi key are served."""
    resp = client.get("/openapi.json")

    assert resp.status_code == 200
    assert "openapi" in resp.json()

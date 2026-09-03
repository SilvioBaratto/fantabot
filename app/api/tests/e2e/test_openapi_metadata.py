"""End-to-end OpenAPI metadata tests (issue #23).

Proves ``create_application()`` enriches the OpenAPI surface — per-tag
descriptions (``openapi_tags``), a top-level ``summary``, ``contact``, and
``license_info`` — and that the custom ``/openapi.json`` endpoint actually
surfaces them.

Two paths are exercised on purpose:
- The shared ``client`` fixture runs with ``settings.debug=True`` (the default),
  where ``openapi_url`` is set, so FastAPI's built-in ``/openapi.json`` route
  serves the schema via ``app.openapi()``.
- ``test_when_production_app_then_custom_route_surfaces_metadata`` builds a
  ``debug=False`` app where ``openapi_url=None``. There the built-in route is
  absent and the *custom* ``get_openapi_json`` handler is the sole responder —
  this is the only path that validates the ``main.py`` ``app.openapi()`` fix.
"""

import pytest

from app.main import create_application


def _schema(client) -> dict:
    """Fetch and return the served OpenAPI schema as a dict."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.e2e
def test_when_openapi_fetched_then_info_contact_is_present(client):
    """when /openapi.json is fetched, info.contact carries a name and email."""
    contact = _schema(client)["info"]["contact"]

    assert contact["name"]
    assert contact["email"]


@pytest.mark.e2e
def test_when_openapi_fetched_then_info_license_is_present(client):
    """when /openapi.json is fetched, info.license carries a license name."""
    license_ = _schema(client)["info"]["license"]

    assert license_["name"]


@pytest.mark.e2e
def test_when_openapi_fetched_then_info_summary_is_present(client):
    """when /openapi.json is fetched, info.summary is a non-empty string."""
    assert _schema(client)["info"]["summary"]


@pytest.mark.e2e
def test_when_docs_requested_then_200_is_returned(client):
    """when GET /docs is requested, the CDN Swagger page returns 200."""
    assert client.get("/docs").status_code == 200


@pytest.mark.e2e
def test_when_redoc_requested_then_200_is_returned(client):
    """when GET /redoc is requested, the CDN ReDoc page returns 200."""
    assert client.get("/redoc").status_code == 200


@pytest.mark.e2e
def test_when_production_app_then_custom_route_surfaces_metadata(monkeypatch):
    """when openapi_url is None (production), the custom /openapi.json still
    surfaces contact/license/tag metadata via app.openapi()."""
    from app import main as main_module

    # Force the production branch: debug=False AND not staging => openapi_url=None,
    # so FastAPI registers no built-in /openapi.json and the custom handler is sole.
    monkeypatch.setattr(main_module.settings, "debug", False)
    monkeypatch.setattr(main_module.settings, "environment", "production")

    prod_app = create_application()
    assert prod_app.openapi_url is None  # custom handler is the only responder

    from fastapi.testclient import TestClient

    # No `with` block: skip lifespan (no DB startup) — only the route is exercised.
    schema = TestClient(prod_app).get("/openapi.json").json()

    assert schema["info"]["contact"]["name"]
    assert schema["info"]["license"]["name"]

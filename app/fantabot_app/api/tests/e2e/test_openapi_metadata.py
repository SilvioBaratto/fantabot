"""End-to-end OpenAPI metadata tests (issue #23).

Proves ``create_application()`` enriches the OpenAPI surface — a top-level
``summary``, ``contact`` and ``license_info`` — and that the custom
``/openapi.json`` endpoint actually surfaces them.

**The production path is the one that carries the file.** It builds a
``debug=False`` app where ``openapi_url=None``, so FastAPI registers no built-in
``/openapi.json`` and the *custom* ``get_openapi_json`` handler is the sole
responder — the only path that validates ``main.py``'s ``app.openapi()`` fix,
and the only one where the 3-arg ``get_openapi(title, version, routes)`` form
this replaced would go red.

**Two debug-path tests were deleted on 2026-09-24, having been measured
redundant against it.** They fetched ``/openapi.json`` through the *built-in*
route and asserted ``info.contact`` and ``info.license`` were present. Dropping
``contact=CONTACT_INFO`` from ``create_application`` turned the debug test **and**
the production test red; so did dropping ``license_info``. What the debug pair
added over the production one was FastAPI's own route serving the same
``app.openapi()`` dict — third-party code, asserted twice.

``summary`` stayed, and the same measurement is why: dropping
``summary=...`` turns **only** that test red, because the production test asserts
contact and license and never reads ``info.summary``. It is the one metadata
field nothing else covers.

⚠ All of these are truthiness assertions, so they catch a field *removed* and not
a placeholder left unedited — ``CONTACT_INFO`` still reads ``support@example.com``.
That is deliberate here (the values are scaffold identity, not a contract) and is
recorded so nobody reads the file as pinning them.
"""

import pytest

from fantabot_app.api.main import create_application


def _schema(client) -> dict:
    """Fetch and return the served OpenAPI schema as a dict."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.e2e
def test_when_openapi_fetched_then_info_summary_is_present(client):
    """when /openapi.json is fetched, info.summary is a non-empty string."""
    assert _schema(client)["info"]["summary"]


@pytest.mark.e2e
def test_when_production_app_then_custom_route_surfaces_metadata(monkeypatch):
    """when openapi_url is None (production), the custom /openapi.json still
    surfaces contact/license metadata via app.openapi()."""
    from fantabot_app.api import main as main_module

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

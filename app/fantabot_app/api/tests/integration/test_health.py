"""Baseline-route tests for the root and health endpoints (issue #11).

Uses the shared ``client`` fixture (``TestClient`` with ``get_db`` overridden).
These endpoints touch no DB and open no sockets, so they belong in the default
fast tier: marked ``e2e`` (which the default ``-m 'not integration'`` run executes),
not ``integration``. ``/health`` is the liveness route the cold-install CI (A1) and
the guarded boot check depend on — it must be guarded by the tier that always runs.
"""

import pytest


@pytest.mark.e2e
def test_when_api_index_is_requested_then_status_is_operational(client):
    """when GET /api is requested, 200 and status 'operational' are returned.

    The welcome JSON lives at /api, not /, so the compiled SPA can own the root URL
    (server.mount_spa). Without the SPA mounted (this fixture), / has no route.
    """
    resp = client.get("/api")

    assert resp.status_code == 200
    assert resp.json()["status"] == "operational"


@pytest.mark.e2e
def test_when_health_is_requested_then_status_ok_is_returned(client):
    """when GET /health is requested, 200 and {'status': 'ok'} are returned."""
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

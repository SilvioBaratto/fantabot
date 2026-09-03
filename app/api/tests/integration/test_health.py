"""Integration tests for the root and health endpoints (issue #11).

Uses the shared ``client`` fixture (``TestClient`` with ``get_db`` overridden).
These endpoints touch no DB; they prove the app serves its baseline routes.
"""

import pytest


@pytest.mark.integration
def test_when_root_is_requested_then_status_is_operational(client):
    """when GET / is requested, 200 and status 'operational' are returned."""
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.json()["status"] == "operational"


@pytest.mark.integration
def test_when_health_is_requested_then_status_ok_is_returned(client):
    """when GET /health is requested, 200 and {'status': 'ok'} are returned."""
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

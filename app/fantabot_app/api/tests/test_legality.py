"""S11 — /asta/legality (the Mantra schema grid, package data)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.legality import load_schemi


def test_load_schemi_returns_eleven_modules() -> None:
    schemi = load_schemi()
    assert len(schemi) == 11
    names = {s.nome for s in schemi}
    assert "3-4-3" in names
    assert "4-2-3-1" in names
    # each schema has 10 outfield slots, each a non-empty list of role codes
    for schema in schemi:
        assert len(schema.slots) == 10
        assert all(slot for slot in schema.slots)


def test_legality_endpoint_returns_grid_and_roles() -> None:
    response = TestClient(app).get("/api/v1/asta/legality")
    assert response.status_code == 200
    body = response.json()
    assert len(body["schemi"]) == 11
    # roles include known Mantra codes
    assert "Pc" in body["roles"]
    assert "Dc" in body["roles"]

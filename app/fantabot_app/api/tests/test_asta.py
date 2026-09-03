"""S9 — /asta/plan (RosterRules injection + degrade)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.asta import build_roster_rules


def test_build_roster_rules_from_snapshot() -> None:
    snapshot = SimpleNamespace(roster_size=25, min_roles=[2, 23], max_roles=[4, 28])
    rules = build_roster_rules(snapshot)
    assert rules.size == 25
    assert rules.min_goalkeepers == 2
    assert rules.min_movement == 23


def test_build_roster_rules_falls_back_without_snapshot() -> None:
    rules = build_roster_rules(None)
    assert rules.size == 30  # the fantabot default


def test_build_roster_rules_falls_back_on_missing_fields() -> None:
    snapshot = SimpleNamespace(roster_size=None, min_roles=None, max_roles=None)
    rules = build_roster_rules(snapshot)
    assert rules.size == 30


def test_asta_plan_degrades_open_on_db_error(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/asta/plan?league_id=4103937")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["players"] == []

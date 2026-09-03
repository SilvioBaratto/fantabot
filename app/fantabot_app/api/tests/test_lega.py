"""S5 — /lega and /lega/{id}/rosters."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.lega import build_overview, build_rosters


def test_build_overview_maps_snapshot() -> None:
    snapshot = SimpleNamespace(
        captured_at=datetime(2026, 9, 2, tzinfo=UTC),
        matchday=3,
        budget=500,
        roster_size=25,
        min_roles=[2, 23],
        max_roles=[4, 28],
        modules=["343", "4231"],
        bench_size=12,
    )
    overview = build_overview(4103937, snapshot, team_count=8)
    assert overview.league_id == 4103937
    assert overview.roster_size == 25
    assert overview.min_roles == [2, 23]
    assert overview.team_count == 8


def test_build_overview_handles_missing_snapshot() -> None:
    overview = build_overview(999, None, team_count=0)
    assert overview.league_id == 999
    assert overview.roster_size is None
    assert overview.team_count == 0


def test_build_rosters_zips_ids_and_costs() -> None:
    team = SimpleNamespace(
        team_id=1,
        nome="Squadra A",
        owner="Owner A",
        credits_initial=500,
        credits_spent=480,
        credits_remaining=20,
        roster_ids=[111, 222, 333],
        roster_costs=[50, 30],  # deliberately shorter than ids
    )
    rosters = build_rosters([team])
    assert rosters[0].nome == "Squadra A"
    assert [s.player_id for s in rosters[0].roster] == [111, 222, 333]
    assert [s.cost for s in rosters[0].roster] == [50, 30, None]


def test_lega_endpoints_degrade_open_on_db_error(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    client = TestClient(app)
    assert client.get("/api/v1/lega").json() == []
    assert client.get("/api/v1/lega/4103937/rosters").json() == []

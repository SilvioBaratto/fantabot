"""S12 — /lineup/plan (preview only; degrades open, never submits)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.lineup import build_lineup_plan


def test_build_lineup_plan_maps_starters_and_bench() -> None:
    planned = SimpleNamespace(
        module="4-3-3",
        starts=(1, 2, 3),
        bench=(4, 5),
        mday=3,
    )
    names = {1: "Svilar", 2: "Dimarco", 3: "Dybala", 4: "Reserve A", 5: "Reserve B"}
    plan = build_lineup_plan(planned, names)
    assert plan.found is True
    assert plan.module == "4-3-3"
    assert plan.matchday == 3
    assert [p.nome for p in plan.starters] == ["Svilar", "Dimarco", "Dybala"]
    assert [p.nome for p in plan.bench] == ["Reserve A", "Reserve B"]


def test_lineup_plan_degrades_open_without_a_platform_call(monkeypatch) -> None:
    # Force the DB layer to fail so no apileague/network call is reached: the test makes no
    # live request to the platform and exercises the degrade-open path. (With no key
    # configured the endpoint returns early anyway — either way, found is false.)
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/lineup/plan?league_id=4103937")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["starters"] == []
    assert body["reason"]

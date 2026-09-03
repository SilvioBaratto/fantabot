"""S10 — /asta/target-prices."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.pricing import build_report


def _row(nome: str, qi: int, target: int):
    return SimpleNamespace(
        id="1",
        nome=nome,
        squadra="ROM",
        role="A",
        macro_role="A",
        qi=qi,
        prior_media_fantavoto=6.5,
        predicted_pct_delta=0.1,
        team_factor=1.0,
        target_price=target,
        flags="",
    )


def test_build_report_maps_bumps_cuts_and_fades() -> None:
    report = SimpleNamespace(
        system="classic",
        stored=42,
        fades=[SimpleNamespace(role="A", observations=120, fade=None)],
        team_factors={"ROM": 1.0},
        biggest_bumps=[_row("Dybala", 20, 28)],
        biggest_cuts=[_row("Someone", 15, 8)],
        flag_counts={"floor_qi": 3},
    )
    out = build_report(report)
    assert out.found is True
    assert out.system == "classic"
    assert out.stored == 42
    assert out.biggest_bumps[0].nome == "Dybala"
    assert out.fades[0].observations == 120
    assert out.flag_counts["floor_qi"] == 3


def test_target_prices_degrades_open_on_error(monkeypatch) -> None:
    from fantabot.application import pricing

    def boom(**_kwargs):
        raise RuntimeError("no data")

    monkeypatch.setattr(pricing, "run", boom)

    response = TestClient(app).get("/api/v1/asta/target-prices")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["biggest_bumps"] == []

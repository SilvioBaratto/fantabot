"""S7 — /news and /news/drifted."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.news import build_drift, build_news


def _sentiment(player_id: str, nome: str, sentiment: float):
    from fantabot.domain.shared.values import SentimentRow

    return SentimentRow(
        player_id=player_id,
        nome=nome,
        data_run="2026-09-03",
        sentiment=sentiment,
        disponibilita=1.0,
        titolarita=0.8,
        mercato=0.0,
        forma=0.5,
        rigorista=0.0,
        piazzati=0.0,
        confidenza=0.9,
        ruolo_campo="W",
        ruoli_mantra="W;T",
        deriva_ruolo=0.0,
    )


def test_build_news_sorts_by_sentiment_desc_and_limits() -> None:
    latest = {
        "1": _sentiment("1", "Rabiot", 0.2),
        "2": _sentiment("2", "Zaccagni", 0.9),
        "3": _sentiment("3", "Osimhen", 0.5),
    }
    rows = build_news(latest, limit=2)
    assert [r.nome for r in rows] == ["Zaccagni", "Osimhen"]


def test_build_drift_maps_rows() -> None:
    from fantabot.domain.shared.values import RoleDrift

    drifts = [RoleDrift(player_id="7", nome="Zaccagni", ruoli_mantra="C", ruolo_campo="W", deriva_ruolo=2.0)]
    rows = build_drift(drifts)
    assert rows[0].nome == "Zaccagni"
    assert rows[0].ruolo_campo == "W"
    assert rows[0].deriva_ruolo == 2.0


def test_news_endpoints_degrade_open_on_db_error(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    client = TestClient(app)
    assert client.get("/api/v1/news").json() == []
    assert client.get("/api/v1/news/drifted").json() == []

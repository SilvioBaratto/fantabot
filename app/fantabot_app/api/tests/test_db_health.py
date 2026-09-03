"""S1 — the /db/health endpoint (the cockpit System panel)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.db import read_db_health


class FakeAdmin:
    def __init__(self, *, ok: bool = True, latency: float = 5.0, stats=None, raise_stats=False):
        self._ok = ok
        self._latency = latency
        self._stats = stats or []
        self._raise = raise_stats

    def health(self) -> tuple[bool, float]:
        return (self._ok, self._latency)

    def table_stats(self) -> list[dict[str, Any]]:
        if self._raise:
            raise RuntimeError("stats blew up")
        return self._stats


def test_read_db_health_maps_tables() -> None:
    admin = FakeAdmin(
        ok=True,
        latency=12.5,
        stats=[
            {"name": "quotazioni", "exists": True, "row_count": 571, "size_pretty": "128 kB"},
            {"name": "league_snapshot", "exists": False, "row_count": None, "size_pretty": "—"},
        ],
    )
    health = read_db_health(admin)
    assert health.ok is True
    assert health.latency_ms == 12.5
    assert [t.name for t in health.tables] == ["quotazioni", "league_snapshot"]
    assert health.tables[0].row_count == 571


def test_read_db_health_degrades_when_table_stats_raises() -> None:
    admin = FakeAdmin(ok=True, latency=3.0, raise_stats=True)
    health = read_db_health(admin)
    assert health.ok is True  # the probe still says the DB answered
    assert health.tables == []  # stats hiccup swallowed


def test_read_db_health_reports_down_database() -> None:
    admin = FakeAdmin(ok=False, latency=9.9)
    health = read_db_health(admin)
    assert health.ok is False
    assert health.tables == []


def test_db_health_endpoint_degrades_open_on_db_error(monkeypatch) -> None:
    # Force the DB layer to fail: the endpoint must never 500, and the test opens no
    # socket (zero-socket default tier).
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/db/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["tables"] == []

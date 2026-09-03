"""S6 — POST /actions/lega-sync (job runner + collect/persist)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from fantabot_app.api.main import app


def _job(client: TestClient, job_id: str) -> dict:
    return client.get(f"/api/v1/jobs/{job_id}").json()


def _wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_lega_sync_runs_collect_then_persist_and_reports_ok(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories import league as league_repo_mod
    from fantabot.adapters.tokens import store as store_mod
    from fantabot.application import lega_sync
    from fantabot.domain.tokens import crypto

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(database_manager, "get_session", fake_session)
    monkeypatch.setattr(crypto, "TokenCipher", lambda key: None)
    monkeypatch.setattr(store_mod, "TokenStore", lambda session, cipher: None)
    monkeypatch.setattr(league_repo_mod, "LeagueRepository", lambda session: None)
    monkeypatch.setattr(
        lega_sync, "collect", lambda league_id, *, store, reporter: SimpleNamespace(ok=True)
    )
    monkeypatch.setattr(lega_sync, "persist", lambda result, repository: {"league_snapshot": 6})

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/lega-sync?league_id=4103937").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    job = _job(client, job_id)
    assert job["ok"] is True
    assert any("wrote 6 rows" in line for line in job["lines"])


def test_lega_sync_fails_cleanly_without_a_key(monkeypatch) -> None:
    from fantabot.domain.tokens import crypto

    def boom(_key):
        raise RuntimeError("no key configured")

    monkeypatch.setattr(crypto, "TokenCipher", boom)

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/lega-sync?league_id=4103937").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "error")


class _FakeSentimentRepo:
    def __init__(self, session) -> None:
        pass

    def existing_keys(self, day) -> set:
        return set()

    def upsert_rows(self, rows, force: bool = False) -> int:
        return len(rows)


def test_news_fetch_runs_and_completes(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager, news_pool
    from fantabot.adapters.persistence.repositories import sentiment as sentiment_mod
    from fantabot.application import news_fetcher

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(database_manager, "get_session", fake_session)
    monkeypatch.setattr(
        news_pool, "load_pool", lambda session, season: [SimpleNamespace(id="1", nome="X")]
    )
    monkeypatch.setattr(sentiment_mod, "SentimentRepository", _FakeSentimentRepo)

    async def fake_fetch(players, **_kwargs):
        return SimpleNamespace(failures=[])

    monkeypatch.setattr(news_fetcher, "fetch_all", fake_fetch)

    client = TestClient(app)
    job_id = client.post("/api/v1/actions/news-fetch?season=2026/27").json()["job_id"]

    assert _wait(lambda: _job(client, job_id)["status"] == "done")
    assert _job(client, job_id)["ok"] is True

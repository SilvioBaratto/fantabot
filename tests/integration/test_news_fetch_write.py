"""`fantabot news-fetch --write` end to end, with the agent call faked out.

Everything downstream of the query is real: the CLI, the repository, the upsert
and the database. Only ``fetch_all`` is replaced, because a genuine run spends
523 agent queries and CLAUDE.md's rule is that the suite makes none.

What this closes is criterion 11 — the same day twice inserts once, and --force
updates in place rather than appending a second row that the reader would keep.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from fantabot.cli import app
from fantabot.db import database_manager
from fantabot.news.pipeline import FetchResult

pytestmark = pytest.mark.db

runner = CliRunner()
TODAY = date.today().isoformat()


def _row(player_id: str, riassunto: str) -> dict[str, str]:
    return {
        "data_run": TODAY,
        "giorni_lookback": "14",
        "stagione": "2026/27",
        "id": player_id,
        "nome": "Canary",
        "squadra": "ATA",
        "ruolo": "Difensore",
        "ruoli_mantra": "B;DS",
        "ruolo_campo": "B",
        "deriva_ruolo": "0.00",
        "sentiment": "0.10",
        "disponibilita": "1.00",
        "titolarita": "0.90",
        "mercato": "0.00",
        "forma": "0.10",
        "rigorista": "0.00",
        "piazzati": "0.00",
        "confidenza": "0.80",
        "riassunto": riassunto,
        "n_fonti": "1",
        "fonti": "https://a",
        "modello": "fake",
    }


@pytest.fixture
def canary_player() -> Any:
    """A real player id, with any leftover reading for today removed first."""
    with database_manager.get_session() as session:
        player_id = str(
            session.execute(text("SELECT id FROM players ORDER BY id LIMIT 1")).scalar()
        )
        session.execute(
            text("DELETE FROM player_sentiment WHERE data_run = :d AND player_id = :p"),
            {"d": TODAY, "p": int(player_id)},
        )
    yield player_id
    with database_manager.get_session() as session:
        session.execute(
            text("DELETE FROM player_sentiment WHERE data_run = :d AND player_id = :p"),
            {"d": TODAY, "p": int(player_id)},
        )


def _fake_fetch(rows: list[dict[str, str]]) -> Any:
    async def fetch_all(*args: object, **kwargs: object) -> FetchResult:
        return FetchResult(rows=rows)

    return fetch_all


def _stored(player_id: str) -> list[str]:
    with database_manager.get_session() as session:
        return list(
            session.execute(
                text(
                    "SELECT riassunto FROM player_sentiment "
                    "WHERE data_run = :d AND player_id = :p"
                ),
                {"d": TODAY, "p": int(player_id)},
            ).scalars()
        )


def test_the_same_day_twice_stores_one_row(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    from fantabot.news import pipeline

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "prima")]))
    assert runner.invoke(app, ["news-fetch", "--write", "--limit", "1"]).exit_code == 0

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "seconda")]))
    assert runner.invoke(app, ["news-fetch", "--write", "--limit", "1"]).exit_code == 0

    assert _stored(canary_player) == ["prima"]


def test_force_updates_in_place_rather_than_appending(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """Today --force skips the resume filter and append_rows has no dedup, so
    it writes a duplicate the reader then keeps. This is the fix."""
    from fantabot.news import pipeline

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "prima")]))
    runner.invoke(app, ["news-fetch", "--write", "--limit", "1"])

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "corretta")]))
    result = runner.invoke(app, ["news-fetch", "--write", "--force", "--limit", "1"])

    assert result.exit_code == 0
    assert _stored(canary_player) == ["corretta"]


def test_scope_pool_builds_the_pool_from_postgres() -> None:
    """Moved from the default tier: the pool is a query now, so news-fetch
    needs the stack up even for --no-run."""
    result = runner.invoke(app, ["news-fetch", "--scope", "pool", "--limit", "1", "--no-run"])

    assert result.exit_code == 0


def test_print_prompt_with_no_run_spends_nothing() -> None:
    result = runner.invoke(app, ["news-fetch", "--limit", "1", "--print-prompt", "--no-run"])

    assert result.exit_code == 0
    assert "GIOCATORE" in result.output
    assert "Fonti preferite" in result.output


def _player(player_id: str) -> Any:
    from fantabot.news.pool import PoolPlayer

    return PoolPlayer(
        id=player_id, nome="Canary", squadra="ATA", ruolo="Difensore", ruoli_mantra="B;DS"
    )


def _progress(player_id: str, row: dict[str, str] | None, failure: str | None) -> Any:
    from fantabot.news.pipeline import PlayerOutcome, Progress

    return Progress(
        done=1,
        total=1,
        outcome=PlayerOutcome(
            player=_player(player_id), row=row, failure=failure, rate_limited=False
        ),
    )


def test_a_crash_mid_run_still_stores_what_had_been_fetched(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """The whole point of storing as it goes.

    ``--flush-every 50`` keeps the reading in the sink's pending list, so only
    the drain on the way out can save it. Everything after ``asyncio.run`` — the
    final extend, the drain, the report — is skipped when a coroutine raises.
    """
    from fantabot.news import pipeline

    row = _row(canary_player, "salvata")

    async def crashing(players: object, **kwargs: Any) -> None:
        kwargs["on_result"](_progress(canary_player, row, None))
        raise RuntimeError("the run died before its summary")

    monkeypatch.setattr(pipeline, "fetch_all", crashing)
    result = runner.invoke(
        app, ["news-fetch", "--write", "--limit", "2", "--flush-every", "50"]
    )

    assert result.exit_code != 0
    assert _stored(canary_player) == ["salvata"], "the fetched reading was discarded"


def test_a_failure_full_of_brackets_does_not_take_the_run_with_it(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """`outcome.failure` is agent-written text. Rich reads `[/serie-a/news]` as a
    closing tag and raises MarkupError, which inside the gathered coroutine ends
    a run that may be hours old — and it eats the `[type=..., input_value=...]`
    tail of every pydantic rejection, which is the whole diagnostic."""
    from fantabot.news import pipeline

    row = _row(canary_player, "sopravvissuta")
    hostile = "structured output failed the schema: [/serie-a/news] [type=float_parsing]"

    async def with_hostile_text(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_result"](_progress(canary_player, None, hostile))
        kwargs["on_result"](_progress(canary_player, row, None))
        return FetchResult(rows=[row], failures=[("Canary", hostile)])

    monkeypatch.setattr(pipeline, "fetch_all", with_hostile_text)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert _stored(canary_player) == ["sopravvissuta"]
    assert "serie-a/news" in result.output, "the diagnostic was swallowed by the markup parser"
    assert "float_parsing" in result.output

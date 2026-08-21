"""T7: the fan-out.

The runner is injected, so every case here runs without an SDK subprocess. What
matters is the failure behaviour: 523 players a week means a bad one is routine,
and a routine event must not end the run.
"""

import asyncio
from datetime import date
from typing import Any

from fantabot.agentkit.options import AgentRequest
from fantabot.agentkit.runner import Outcome
from fantabot.news.models import PlayerSentiment
from fantabot.news.pipeline import fetch_all
from fantabot.news.pool import PoolPlayer

RUN_DAY = date(2026, 10, 7)


def _players(n: int) -> list[PoolPlayer]:
    return [
        PoolPlayer(
            id=str(i),
            nome=f"P{i}",
            squadra="ATA",
            ruolo="Difensore",
            ruoli_mantra="DC",
        )
        for i in range(n)
    ]


def _sentiment(**overrides: Any) -> PlayerSentiment:
    base: dict[str, Any] = {
        "sentiment": 0.1,
        "disponibilita": 0.9,
        "titolarita": 0.8,
        "mercato": 0.0,
        "forma": 0.2,
        "rigorista": 0.0,
        "piazzati": 0.0,
        "confidenza": 0.6,
        "riassunto": "Titolare il 05/10.",
        "fonti": ["https://a"],
        "ruolo_campo": [],
    }
    return PlayerSentiment.model_validate({**base, **overrides})


def _ok_runner(**kwargs: Any) -> Any:
    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        return Outcome(value=_sentiment(**kwargs), failure=None)

    return runner


def _run(players: list[PoolPlayer], runner: Any, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "runner": runner,
        "concurrency": 4,
        "lookback_days": 14,
        "today": RUN_DAY,
        "model": "claude-sonnet-5",
        "stagione": "2026/27",
        "sleep": _no_sleep,
    }
    return asyncio.run(fetch_all(players, **{**defaults, **kwargs}))


async def _no_sleep(_seconds: float) -> None:
    return None


def test_every_player_produces_a_row() -> None:
    result = _run(_players(5), _ok_runner())

    assert len(result.rows) == 5
    assert result.failures == []


def test_a_single_player_failing_does_not_end_the_run() -> None:
    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        if request.label == "P4":
            return Outcome(value=None, failure="agent returned subtype 'error_max_turns'")
        return Outcome(value=_sentiment(), failure=None)

    result = _run(_players(10), runner)

    assert len(result.rows) == 9
    assert [name for name, _ in result.failures] == ["P4"]
    assert "error_max_turns" in result.failures[0][1]


def test_a_player_raising_does_not_end_the_run() -> None:
    # A transport exception is not an Outcome; it still must not take the run down.
    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        if request.label == "P4":
            raise RuntimeError("connection reset")
        return Outcome(value=_sentiment(), failure=None)

    result = _run(_players(10), runner)

    assert len(result.rows) == 9
    assert [name for name, _ in result.failures] == ["P4"]
    assert "connection reset" in result.failures[0][1]


def test_concurrency_is_capped() -> None:
    live = 0
    peak = 0

    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0)
        live -= 1
        return Outcome(value=_sentiment(), failure=None)

    _run(_players(20), runner, concurrency=3)

    assert peak <= 3


def test_a_rate_limited_outcome_backs_off_instead_of_raising() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        return Outcome(value=_sentiment(), failure=None, rate_limited=True)

    result = _run(_players(3), runner, sleep=sleep, backoff_seconds=7.0)

    assert len(result.rows) == 3
    assert result.rate_limited is True
    assert slept == [7.0, 7.0, 7.0]


def test_no_backoff_when_nothing_was_rate_limited() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result = _run(_players(3), _ok_runner(), sleep=sleep)

    assert slept == []
    assert result.rate_limited is False


def test_rows_carry_the_run_metadata_and_computed_drift() -> None:
    result = _run(_players(1), _ok_runner(ruolo_campo=["T"], confidenza=0.8))

    row = result.rows[0]
    assert row["data_run"] == "2026-10-07"
    assert row["giorni_lookback"] == "14"
    assert row["modello"] == "claude-sonnet-5"
    # tagged DC, observed T -> the tag no longer describes him
    assert row["deriva_ruolo"] == "0.80"


def test_rows_come_back_in_pool_order() -> None:
    # Concurrency must not scramble the output; diffs and resume depend on order.
    async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
        # finish in reverse order of dispatch
        await asyncio.sleep((10 - int(request.label[1:])) / 1000)
        return Outcome(value=_sentiment(), failure=None)

    result = _run(_players(10), runner, concurrency=10)

    assert [row["nome"] for row in result.rows] == [f"P{i}" for i in range(10)]


def test_an_empty_player_list_is_a_no_op() -> None:
    result = _run([], _ok_runner())

    assert result.rows == []
    assert result.failures == []

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


class TestTheRunSpeaksWhileItWorks:
    """548 queries at two a minute is nearly two hours with nothing on stdout
    between the opening line and the summary.

    `aste-collect` learned this already and CLAUDE.md records it: *a run with no
    end must speak while it runs*, because the summary it prints at exit is the
    one thing an interrupted run never reaches. `news-fetch` is the same shape
    and had the same silence — on 2026-08-28 the only way to tell a working run
    from a stalled one was to count subprocesses.

    The hooks are injected for the same reason the runner is: the fan-out stays
    testable with no agent, no console and no database.
    """

    def test_a_player_is_announced_before_the_query_that_takes_minutes(self) -> None:
        started: list[str] = []
        _run(_players(3), _ok_runner(), on_start=lambda p: started.append(p.nome))

        assert started == ["P0", "P1", "P2"]

    def test_each_completion_is_reported_as_it_lands(self) -> None:
        seen: list[tuple[int, int, str]] = []
        _run(
            _players(4),
            _ok_runner(),
            concurrency=1,
            on_result=lambda pr: seen.append((pr.done, pr.total, pr.outcome.player.nome)),
        )

        assert seen == [(1, 4, "P0"), (2, 4, "P1"), (3, 4, "P2"), (4, 4, "P3")]

    def test_the_row_is_built_by_the_time_the_completion_is_reported(self) -> None:
        """The sink stores from this callback, so the row has to exist here and
        not only in the summary the run may never reach."""
        rows: list[Any] = []
        _run(_players(2), _ok_runner(), on_result=lambda pr: rows.append(pr.outcome.row))

        assert all(row is not None for row in rows)
        assert {row["id"] for row in rows} == {"0", "1"}
        assert rows[0]["modello"] == "claude-sonnet-5"

    def test_a_failure_is_reported_as_it_happens_rather_than_only_at_the_end(self) -> None:
        reported: list[tuple[str, str | None]] = []

        async def half_failing(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            if request.label == "P1":
                return Outcome(value=None, failure="schema rejected")
            return Outcome(value=_sentiment(), failure=None)

        result = _run(
            _players(3),
            half_failing,
            on_result=lambda pr: reported.append((pr.outcome.player.nome, pr.outcome.failure)),
        )

        assert ("P1", "schema rejected") in reported
        assert sorted(reported) == [("P0", None), ("P1", "schema rejected"), ("P2", None)]
        assert result.failures == [("P1", "schema rejected")]

    def test_a_raising_query_is_reported_too(self) -> None:
        """The transport failure shape: it must not skip the progress line and
        leave the count short of the total for ever."""
        reported: list[str] = []

        async def raising(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            raise TimeoutError("stream closed")

        _run(_players(2), raising, on_result=lambda pr: reported.append(pr.outcome.player.nome))

        assert sorted(reported) == ["P0", "P1"]

    def test_the_hooks_are_optional(self) -> None:
        """Every existing caller passes neither."""
        result = _run(_players(3), _ok_runner())

        assert len(result.rows) == 3

    def test_reporting_still_leaves_the_rows_in_pool_order(self) -> None:
        """Completion order drives the callback; the returned rows must not
        inherit it — the resume index and every diff depend on pool order."""

        async def slow_first(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            if request.label == "P0":
                await asyncio.sleep(0.02)
            return Outcome(value=_sentiment(), failure=None)

        done: list[str] = []
        result = _run(
            _players(3),
            slow_first,
            on_result=lambda pr: done.append(pr.outcome.player.nome),
        )

        assert done[0] != "P0", "the fixture did not actually reorder completion"
        assert [row["id"] for row in result.rows] == ["0", "1", "2"]

    def test_the_count_reaches_the_total_even_when_everything_fails(self) -> None:
        reported: list[int] = []

        async def all_failing(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            return Outcome(value=None, failure="no")

        _run(_players(5), all_failing, on_result=lambda pr: reported.append(pr.done))

        assert sorted(reported) == [1, 2, 3, 4, 5]


class TestBuildingARowIsAlsoARoutineFailure:
    """The module promises *nothing here lets one player end the run*, and named
    two absorbed shapes: an ``Outcome`` carrying a reason, and a raised transport
    error. There is a third, and it was outside the guard.

    ``PlayerSentiment.ruolo_campo`` is an unconstrained ``list[str]``. The prompt
    lists the twelve Mantra codes but nothing validates them, so a model
    answering ``["Trequartista"]`` passes schema validation and then makes
    ``build_row`` raise ``UnknownRoleCode`` — reproduced directly, not inferred.

    Built after `gather` that only wasted the run's own summary. Built inside the
    coroutine, where a sink is now storing from, it aborts the gather and strands
    every reading held but not yet flushed.
    """

    def _one_bad(self, bad_label: str) -> Any:
        async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            if request.label == bad_label:
                return Outcome(value=_sentiment(ruolo_campo=["Trequartista"]), failure=None)
            return Outcome(value=_sentiment(), failure=None)

        return runner

    def test_an_unbuildable_row_is_counted_rather_than_raised(self) -> None:
        result = _run(_players(3), self._one_bad("P1"))

        assert [row["id"] for row in result.rows] == ["0", "2"]
        assert len(result.failures) == 1
        assert result.failures[0][0] == "P1"
        assert "UnknownRoleCode" in result.failures[0][1]

    def test_the_players_after_it_still_run(self) -> None:
        """The whole point: a gather that aborts takes the queue with it."""
        reported: list[str] = []
        _run(
            _players(5),
            self._one_bad("P0"),
            concurrency=1,
            on_result=lambda pr: reported.append(pr.outcome.player.nome),
        )

        assert reported == ["P0", "P1", "P2", "P3", "P4"]

    def test_it_is_reported_through_the_same_hook_as_any_other_failure(self) -> None:
        seen: list[tuple[str, bool]] = []
        _run(
            _players(2),
            self._one_bad("P1"),
            on_result=lambda pr: seen.append((pr.outcome.player.nome, pr.outcome.row is None)),
        )

        assert sorted(seen) == [("P0", False), ("P1", True)]


class TestASystemicFailureStopsTheRun:
    """A failing player is routine. Every player failing is not.

    On 2026-08-28 the Ollama shim behind `FANTABOT_AGENT_BASE_URL` returned
    HTTP 429 — `you have reached your session usage limit` — for every query
    from player 76 onward. None of that reached fantabot: the CLI turned each
    429 into a result with `subtype == "success"` and no structured output, so
    it arrived as the ordinary reason `agent returned no structured output`,
    the same string a single confused player produces.

    `RateLimitEvent` never fired, so the 30-second backoff never fired either,
    and this module's rule — *nothing here lets one player end the run* —
    dutifully carried the run through 15 straight failures. Left alone it would
    have spent 458 more queries against an exhausted quota and finished
    reporting a successful collection of a third of the pool.

    The reason text cannot distinguish the two cases; the shape of the failures
    can. A run of consecutive failures with no success between them is not a
    player problem, and the only useful thing to do with it is stop and say so.
    """

    def _always_failing(self, reason: str = "agent returned no structured output") -> Any:
        async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            return Outcome(value=None, failure=reason)

        return runner

    def test_it_stops_once_the_failures_run_unbroken(self) -> None:
        attempted: list[str] = []

        async def counting(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            attempted.append(request.label)
            return Outcome(value=None, failure="agent returned no structured output")

        result = _run(_players(100), counting, concurrency=1, max_consecutive_failures=5)

        assert len(attempted) < 100, "the whole pool was queried against a dead backend"
        assert len(result.failures) == 5

    def test_it_says_why_it_stopped(self) -> None:
        result = _run(_players(40), self._always_failing(), concurrency=1,
                      max_consecutive_failures=5)

        assert result.stopped_early is not None
        assert "5" in result.stopped_early
        assert "agent returned no structured output" in result.stopped_early, (
            "the operator needs the reason, not just the count"
        )

    def test_players_it_never_attempted_are_not_counted_as_failures(self) -> None:
        """Reporting 95 failures for 5 attempts would misdescribe the outage and
        make the resume filter look like it had nothing left to do."""
        result = _run(_players(100), self._always_failing(), concurrency=1,
                      max_consecutive_failures=5)

        assert len(result.failures) == 5
        assert result.skipped == 95
        assert len(result.failures) + result.skipped == 100

    def test_a_success_resets_the_run(self) -> None:
        """Scattered failures are the normal weekly shape and must not trip it."""

        async def every_other(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            index = int(request.label[1:])
            if index % 2:
                return Outcome(value=None, failure="schema rejected")
            return Outcome(value=_sentiment(), failure=None)

        result = _run(_players(40), every_other, concurrency=1, max_consecutive_failures=5)

        assert result.stopped_early is None
        assert len(result.rows) == 20
        assert len(result.failures) == 20

    def test_the_readings_gathered_before_the_wall_are_still_returned(self) -> None:
        """The 74 successes of 2026-08-28 had to survive the stop."""

        async def wall_at_ten(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            if int(request.label[1:]) < 10:
                return Outcome(value=_sentiment(), failure=None)
            return Outcome(value=None, failure="agent returned no structured output")

        result = _run(_players(60), wall_at_ten, concurrency=1, max_consecutive_failures=5)

        assert [row["id"] for row in result.rows] == [str(i) for i in range(10)]
        assert result.stopped_early is not None

    def test_it_can_be_turned_off(self) -> None:
        result = _run(_players(30), self._always_failing(), concurrency=1,
                      max_consecutive_failures=0)

        assert result.stopped_early is None
        assert len(result.failures) == 30
        assert result.skipped == 0

    def test_a_pool_smaller_than_the_threshold_still_finishes(self) -> None:
        result = _run(_players(3), self._always_failing(), concurrency=1,
                      max_consecutive_failures=10)

        assert result.stopped_early is None
        assert len(result.failures) == 3

    def test_the_default_is_on(self) -> None:
        """A run left to cron must not need a flag to stop being useless."""
        result = _run(_players(200), self._always_failing(), concurrency=1)

        assert result.stopped_early is not None
        assert result.skipped > 0


class TestAnInterruptStopsItPromptly:
    """Ctrl-C did not stop this command.

    Measured 2026-08-28: two SIGINTs, thirty seconds apart, both ignored. The
    escalation to SIGTERM skipped the `except BaseException` drain — the exact
    path written to protect an interrupt — and four fetched readings queued in
    the sink went with it.

    Cancelling mid-query is not the answer: a query that has already spent its
    web searches should be allowed to finish and be stored. What has to stop is
    *starting new ones*, which is the same thing the failure breaker already
    does. So an interrupt reuses it: nothing new is asked, what is in flight
    completes, and the readings are returned to be stored.
    """

    def test_no_further_player_is_queried_once_a_stop_is_asked_for(self) -> None:
        asked: list[str] = []
        stop = {"now": False}

        async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            asked.append(request.label)
            if len(asked) == 3:
                stop["now"] = True
            return Outcome(value=_sentiment(), failure=None)

        result = _run(
            _players(50), runner, concurrency=1, should_stop=lambda: stop["now"]
        )

        assert asked == ["P0", "P1", "P2"]
        assert result.skipped == 47

    def test_what_was_already_running_is_still_returned(self) -> None:
        """The queries are the expensive half; throwing away a finished one to
        exit a second sooner is the wrong trade."""
        stop = {"now": False}

        async def runner(request: AgentRequest, schema: type[Any]) -> Outcome[Any]:
            stop["now"] = True
            return Outcome(value=_sentiment(), failure=None)

        result = _run(_players(20), runner, concurrency=1, should_stop=lambda: stop["now"])

        assert [row["id"] for row in result.rows] == ["0"]

    def test_it_says_it_was_interrupted_rather_than_naming_a_failure(self) -> None:
        result = _run(_players(20), _ok_runner(), concurrency=1, should_stop=lambda: True)

        assert result.stopped_early is not None
        assert "interrupt" in result.stopped_early.lower()
        assert result.failures == [], "an interrupt is not a failure of any player"

    def test_the_unasked_players_are_skipped_so_a_resume_offers_them_again(self) -> None:
        result = _run(_players(30), _ok_runner(), concurrency=1, should_stop=lambda: True)

        assert result.skipped == 30
        assert result.rows == []

    def test_without_the_hook_nothing_changes(self) -> None:
        result = _run(_players(5), _ok_runner(), concurrency=1)

        assert result.stopped_early is None
        assert result.skipped == 0
        assert len(result.rows) == 5

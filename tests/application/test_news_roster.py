"""T26: one news run over the lega's roster, bounded twice. Zero agent calls, no database.

`news fetch` is the operator's command — it narrates, installs a signal handler and runs
for nearly two hours over 523 players. The hourly refresh wants the same fan-out over ~30
and a verdict rather than a terminal, so `news_roster` holds the orchestration and reuses
`fetch_all` unchanged.

What is pinned here:

* **only roster ids are queried**, and the join is `int` against `str` — `PoolPlayer.id`
  is a string and a roster id an `int`, so a direct `in` matches nothing *quietly*;
* **a runner that never returns is cancelled** at the per-query limit and the run then
  reports no success;
* **an empty roster is a failure**, and it is refused before the gateway is touched at
  all — proved with a gateway that raises if read;
* **no agent-written text reaches the reporter**, because `application/` cannot escape it.

The runner and the gateway are injected. Nothing below builds a session, opens a socket or
calls the SDK — and every fake runner **yields** rather than blocking, because
`asyncio.wait_for` cancels the inner coroutine and waits for the cancellation to land: a
fake that called `time.sleep` would hang the suite rather than fail it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest

from fantabot.adapters.agent.runner import Outcome
from fantabot.application.news_roster import (
    NewsRunResult,
    PoolSnapshot,
    fetch_roster_news,
    select_players,
    timed_runner,
)
from fantabot.domain.news.models import PlayerSentiment
from fantabot.domain.news.pool import PoolPlayer

DAY = date(2026, 9, 20)
SEASON = "2026/27"


def _player(pid: int, nome: str = "Tizio") -> PoolPlayer:
    return PoolPlayer(
        id=str(pid), nome=nome, squadra="Lazio", ruolo="Centrocampista", ruoli_mantra="C"
    )


POOL = (_player(1, "Uno"), _player(2, "Due"), _player(3, "Tre"), _player(4, "Quattro"))


def _sentiment() -> PlayerSentiment:
    return PlayerSentiment(
        sentiment=0.1, disponibilita=0.9, titolarita=0.8, mercato=0.0, forma=0.2,
        rigorista=0.0, piazzati=0.0, confidenza=0.5,
        riassunto="Nessuna notizia rilevante.", fonti=["https://example.invalid/a"],
        ruolo_campo=[],
    )


class _Gateway:
    """Records what it was asked, and answers from a canned pool."""

    def __init__(self, pool: Sequence[PoolPlayer] = POOL, stored: frozenset[Any] = frozenset()):
        self.pool = tuple(pool)
        self.stored_keys = stored
        self.reads: list[tuple[str, date]] = []
        self.stores: list[list[dict[str, str]]] = []

    def read(self, *, season: str, day: date) -> PoolSnapshot:
        self.reads.append((season, day))
        return PoolSnapshot(self.pool, self.stored_keys)

    def store(self, rows: Sequence[dict[str, str]], *, force: bool) -> int:
        self.stores.append([dict(r) for r in rows])
        return len(rows)


class _Untouchable:
    def read(self, **_kw: Any) -> PoolSnapshot:
        raise AssertionError("the gateway was read")

    def store(self, *_a: Any, **_kw: Any) -> int:
        raise AssertionError("the gateway was written")


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *objects: Any, **_kw: Any) -> None:
        self.lines.append(" ".join(str(o) for o in objects))


def _runner(asked: list[str], *, outcome: Any = None):
    async def run(request: Any, _schema: Any) -> Any:
        asked.append(request.user_prompt if hasattr(request, "user_prompt") else "")
        await asyncio.sleep(0)
        return outcome if outcome is not None else Outcome(value=_sentiment(), failure=None)

    return run


def _run(**over: Any) -> NewsRunResult:
    kwargs: dict[str, Any] = {
        "gateway": _Gateway(),
        "reporter": _Recorder(),
        "model": "claude-test",
        "season": SEASON,
        "day": DAY,
        "write": True,
        "runner": _runner([]),
        "query_timeout": 0.2,
    }
    kwargs.update(over)
    roster = kwargs.pop("roster_ids", [1, 2])
    return asyncio.run(fetch_roster_news(roster, **kwargs))


class TestTheSelection:
    def test_the_roster_join_crosses_int_and_str(self) -> None:
        """`PoolPlayer.id` is a string and a roster id an `int`. A direct `in` matches
        nothing and matches nothing quietly — an empty roster every week, with no error."""
        selection = select_players([2, 4], PoolSnapshot(POOL, frozenset()), day=DAY, force=False)

        assert [p.id for p in selection.players] == ["2", "4"]
        assert selection.matched == 2

    def test_a_roster_id_outside_the_pool_is_reported_and_not_fatal(self) -> None:
        selection = select_players(
            [2, 999], PoolSnapshot(POOL, frozenset()), day=DAY, force=False
        )

        assert selection.unmatched == (999,)
        assert [p.id for p in selection.players] == ["2"]

    def test_todays_reading_is_skipped_and_counted_as_resumed(self) -> None:
        stored = frozenset({(DAY.isoformat(), "2")})

        selection = select_players([1, 2], PoolSnapshot(POOL, stored), day=DAY, force=False)

        assert [p.id for p in selection.players] == ["1"]
        assert (selection.matched, selection.resumed) == (2, 1)

    def test_a_reading_from_another_day_does_not_resume(self) -> None:
        stored = frozenset({("2026-09-13", "2")})

        selection = select_players([1, 2], PoolSnapshot(POOL, stored), day=DAY, force=False)

        assert [p.id for p in selection.players] == ["1", "2"]

    def test_force_asks_again(self) -> None:
        stored = frozenset({(DAY.isoformat(), "1"), (DAY.isoformat(), "2")})

        selection = select_players([1, 2], PoolSnapshot(POOL, stored), day=DAY, force=True)

        assert [p.id for p in selection.players] == ["1", "2"]
        assert selection.resumed == 0


class TestOnlyTheRosterIsQueried:
    def test_the_pool_beyond_the_roster_is_never_asked(self) -> None:
        asked: list[str] = []

        result = _run(roster_ids=[2], runner=_runner(asked))

        assert len(asked) == 1
        assert result.selected == 1
        assert result.ok

    def test_every_reading_reaches_the_gateway(self) -> None:
        gateway = _Gateway()

        result = _run(roster_ids=[1, 2, 3], gateway=gateway, flush_every=1)

        assert result.delivered == 3
        assert sum(len(batch) for batch in gateway.stores) == 3
        assert {row["id"] for batch in gateway.stores for row in batch} == {"1", "2", "3"}

    def test_a_dry_run_stores_nothing_and_carries_the_rows_back(self) -> None:
        gateway = _Gateway()

        result = _run(roster_ids=[1, 2], gateway=gateway, write=False)

        assert gateway.stores == []
        assert len(result.rows) == 2
        assert result.stored == 0


class TestWhatIsRefused:
    def test_an_empty_roster_is_a_failure(self) -> None:
        result = _run(roster_ids=[])

        assert result.ok is False
        assert result.reason == "no roster to query"

    def test_an_empty_roster_is_refused_before_the_gateway_is_touched(self) -> None:
        """Checked after the read, an empty roster costs a multi-megabyte pool query."""
        result = _run(roster_ids=[], gateway=_Untouchable())

        assert result.ok is False

    def test_a_roster_that_matches_nobody_is_a_failure(self) -> None:
        result = _run(roster_ids=[900, 901])

        assert result.ok is False
        assert "pool" in (result.reason or "")
        assert result.unmatched == (900, 901)

    def test_a_run_that_collected_nothing_is_a_failure(self) -> None:
        """Every player failing is not a player problem, and the marker this writes is what
        tells the next hour not to try again."""
        result = _run(
            roster_ids=[1, 2],
            runner=_runner([], outcome=Outcome(value=None, failure="no structured output")),
        )

        assert result.ok is False
        assert result.reason == "no reading was collected"
        assert len(result.failures) == 2

    def test_a_reading_that_could_not_be_stored_is_a_failure(self) -> None:
        class _Broken(_Gateway):
            def store(self, rows: Sequence[dict[str, str]], *, force: bool) -> int:
                raise RuntimeError("database unreachable")

        result = _run(roster_ids=[1, 2], gateway=_Broken())

        assert result.ok is False
        assert "could not be stored" in (result.reason or "")
        assert result.unstored == 2

    def test_a_roster_already_read_today_is_a_success_with_nothing_asked(self) -> None:
        gateway = _Gateway(stored=frozenset({(DAY.isoformat(), "1"), (DAY.isoformat(), "2")}))
        asked: list[str] = []

        result = _run(roster_ids=[1, 2], gateway=gateway, runner=_runner(asked))

        assert result.ok
        assert (result.resumed, result.selected, asked) == (2, 0, [])


class TestThePerQueryLimit:
    def test_a_runner_that_never_returns_is_cancelled(self) -> None:
        cancelled: list[bool] = []

        async def never(_request: Any, _schema: Any) -> Any:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            raise AssertionError("unreachable")  # pragma: no cover

        result = _run(roster_ids=[1], runner=never, query_timeout=0.05)

        assert cancelled == [True]
        assert result.ok is False
        assert result.delivered == 0

    def test_the_timeout_is_an_ordinary_failure_and_the_run_continues(self) -> None:
        """One hung query must not end a run: the rest of the roster is still asked."""
        calls: list[str] = []

        async def slow_then_fast(request: Any, _schema: Any) -> Any:
            calls.append("x")
            if len(calls) == 1:
                await asyncio.sleep(30)
            await asyncio.sleep(0)
            return Outcome(value=_sentiment(), failure=None)

        result = _run(roster_ids=[1, 2], runner=slow_then_fast, query_timeout=0.05)

        assert len(calls) == 2
        assert result.delivered == 1
        assert [reason for _name, reason in result.failures] == ["no answer within 0s"]
        assert result.ok


class TestTheSoftDeadline:
    def test_a_deadline_stops_asking_and_the_rest_are_skipped_not_failed(self) -> None:
        """`fetch_all` consults `should_stop` when a slot frees, so what is in flight
        finishes and is stored — a query that has spent its web searches is not thrown
        away. The unasked come back as `skipped`, which is not a failure."""
        asked: list[str] = []
        stop = [False]

        async def one_then_stop(request: Any, _schema: Any) -> Any:
            asked.append("x")
            stop[0] = True
            await asyncio.sleep(0)
            return Outcome(value=_sentiment(), failure=None)

        result = _run(
            roster_ids=[1, 2, 3, 4],
            runner=one_then_stop,
            should_stop=lambda: stop[0],
            concurrency=1,
        )

        assert len(asked) == 1
        assert result.skipped == 3
        assert result.failures == ()
        assert result.stopped_early is not None

    def test_the_summary_counts_what_was_asked_and_not_what_was_selected(self) -> None:
        stop = [False]

        async def one_then_stop(request: Any, _schema: Any) -> Any:
            stop[0] = True
            await asyncio.sleep(0)
            return Outcome(value=_sentiment(), failure=None)

        result = _run(
            roster_ids=[1, 2, 3, 4],
            runner=one_then_stop,
            should_stop=lambda: stop[0],
            concurrency=1,
        )

        assert "asked 1" in result.summary()
        assert result.selected == 4


class TestNothingAgentWrittenIsPrinted:
    def test_the_reporter_never_sees_a_failure_reason_or_a_player_name(self) -> None:
        """`application/` cannot import `rich.markup.escape`, and Rich reads the tail of a
        pydantic rejection — `[type=..., input_value=...]` — as a style, raising
        `MarkupError` on a `[/...]` from inside a gathered coroutine."""
        reporter = _Recorder()
        nasty = "rejected [/bold] [type=float_parsing, input_value='x']"

        result = _run(
            roster_ids=[1, 2],
            reporter=reporter,
            runner=_runner([], outcome=Outcome(value=None, failure=nasty)),
        )

        assert nasty not in " ".join(reporter.lines)
        assert "Uno" not in " ".join(reporter.lines)
        assert nasty in {reason for _name, reason in result.failures}

    def test_the_summary_is_markup_free(self) -> None:
        result = _run(
            roster_ids=[1, 2],
            runner=_runner([], outcome=Outcome(value=None, failure="[/bold]x")),
        )

        assert "[" not in result.summary() and "]" not in result.summary()


class TestZeroAgentCalls:
    def test_the_sdk_is_never_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patched where `fetch_all` *reads* it — `news_fetcher` binds `sdk_run` at import,
        so patching `adapters.agent.runner.run` would leave the default untouched and the
        only way to fail this test would be to make a real agent call."""
        import fantabot.application.news_fetcher as fetcher

        def _explode(*_a: Any, **_kw: Any) -> Any:
            raise AssertionError("the SDK runner was called")

        monkeypatch.setattr(fetcher, "sdk_run", _explode)

        assert _run(roster_ids=[1, 2]).ok


class TestWhatTheRunItselfSurvives:
    def test_a_roster_half_outside_the_pool_still_runs(self) -> None:
        """Unmatched ids are reported, never fatal. Refusing on *any* unmatched id would
        stop the weekly run the first time the listone moved under the roster."""
        reporter = _Recorder()
        asked: list[str] = []

        result = _run(roster_ids=[1, 999], reporter=reporter, runner=_runner(asked))

        assert result.ok
        assert (result.matched, result.unmatched) == (1, (999,))
        assert len(asked) == 1
        assert any("not in the" in line for line in reporter.lines)

    def test_an_outer_cancellation_is_not_read_as_a_timeout(self) -> None:
        """`CancelledError` is a `BaseException` and is how a caller's shutdown arrives.
        Catching it alongside `TimeoutError` turns "the refresh was killed" into "the query
        timed out", and the query then reports an ordinary failure inside a process being
        torn down.

        Asserted on `timed_runner` itself rather than through a cancelled `fetch_roster_news`:
        `fetch_all` gathers, and a gather that is cancelled re-raises whatever its children
        did with the cancellation — so the outer view cannot tell a suppressed one from a
        propagated one, and the test passes either way.
        """

        async def slow(_request: Any, _schema: Any) -> Any:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")  # pragma: no cover

        async def main() -> Any:
            task = asyncio.ensure_future(timed_runner(slow, seconds=30.0)(None, None))
            await asyncio.sleep(0.01)
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(main())

    def test_the_timeout_itself_is_still_an_ordinary_failure(self) -> None:
        """The control for the case above: what `timed_runner` *does* absorb."""

        async def slow(_request: Any, _schema: Any) -> Any:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")  # pragma: no cover

        outcome = asyncio.run(timed_runner(slow, seconds=0.05)(None, None))

        assert outcome.value is None
        assert "no answer within" in (outcome.failure or "")

    def test_a_credential_in_the_environment_is_cleared_before_any_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run must use the subscription. An `ANTHROPIC_API_KEY` inherited from the
        parent shell would bill an account nobody meant to bill."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-survive")
        seen: list[str | None] = []

        async def peek(_request: Any, _schema: Any) -> Any:
            import os

            seen.append(os.environ.get("ANTHROPIC_API_KEY"))
            await asyncio.sleep(0)
            return Outcome(value=_sentiment(), failure=None)

        _run(roster_ids=[1], runner=peek)

        assert seen == [None]

    def test_a_bug_in_one_query_is_one_failed_player_and_not_a_dead_run(self) -> None:
        """Measured, and deliberately **not** AD8's rule. `fetch_all` catches `Exception`
        around each player because a failing player is routine at 523 a week, and an
        `AssertionError` raised inside one query is absorbed with the rest. AD8's
        "containment re-raises `AssertionError`" is about the *source* boundary T27 wraps
        this whole call in — not about one player — and pinning it here says where to look.
        """

        async def buggy(_request: Any, _schema: Any) -> Any:
            await asyncio.sleep(0)
            raise AssertionError("a real bug")

        result = _run(roster_ids=[1, 2], runner=buggy)

        assert result.ok is False
        assert [reason for _name, reason in result.failures] == (
            ["AssertionError: a real bug"] * 2
        )

    def test_a_keyboard_interrupt_is_not_absorbed(self) -> None:
        """The half of AD8 that already holds here: `except Exception` does not catch a
        `BaseException`, so an operator's Ctrl-C ends the run rather than failing a player."""

        async def interrupted(_request: Any, _schema: Any) -> Any:
            await asyncio.sleep(0)
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            _run(roster_ids=[1], runner=interrupted)

"""One news run over the lega's roster, for a job nobody is watching.

`news fetch` queries the whole 523-player pool and is the operator's command: it narrates,
installs a SIGINT handler, and runs for nearly two hours. The hourly refresh (T27) wants
the same fan-out over ~30 players, bounded twice and reporting a verdict rather than a
terminal. So the orchestration lives here — the roster filter, the resume filter,
`strip_dangerous_env`, the sink, the deadline — and `fetch_all` is reused unchanged.

**Two limits, because they fail differently.**

* A **soft deadline**, handed to `fetch_all`'s own `should_stop`: it stops *asking* for
  more and lets what is in flight finish, because a query that has already spent its web
  searches should be stored rather than thrown away. Players never asked come back as
  `skipped`, which is not a failure.
* A **per-query timeout**, wrapping the runner. `fetch_all` has no wall of its own: one
  hung query holds a semaphore slot for ever and the soft deadline never fires, because
  `should_stop` is only consulted when a slot frees. The timeout is what makes the soft
  deadline reachable.

The run's hard wall is `budget + query_timeout + backoff`, not `budget + query_timeout`:
`fetch_all`'s rate-limit backoff runs after the runner returns and inside the semaphore,
so it sits outside the `wait_for`. T27 sizes its group kill above all three.

**Nothing here is a second copy of a decision.** The Typer body for `news fetch` keeps its
own orchestration — it has a signal handler, a `--print-prompt`, a resume banner and a
cost line, none of which a child process wants — but the two never disagree about anything
the *app* also asks, because the app only ever calls this. What they share is `fetch_all`,
which is where the fan-out's decisions actually live.

**No agent-written text reaches the `Reporter`.** `application/` may not import
`rich.markup.escape` (`tests/test_layers.py`), and a failure reason is agent-written: Rich
reads `[type=..., input_value=...]` — the tail of every pydantic rejection — as a style,
and raises `MarkupError` on a `[/...]`. So the reasons are returned in `failures` for the
caller to escape, and `summary()` carries counts only.

Imports of `news_fetcher` and of persistence are inside function bodies: the first pulls in
`claude_agent_sdk` and the second sqlalchemy, and the hourly `lineup submit` must load
neither.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

from fantabot.application.reporting import Reporter
from fantabot.domain.news.pool import PoolPlayer
from fantabot.domain.news.sink import SentimentSink

if TYPE_CHECKING:
    from fantabot.adapters.agent.runner import Usage
    from fantabot.application.news_fetcher import Runner

#: `news fetch`'s own defaults, so the two surfaces query the same way.
DEFAULT_CONCURRENCY = 4
DEFAULT_FLUSH_EVERY = 5
DEFAULT_LOOKBACK_DAYS = 14

#: A declared backstop, not a measurement: long enough that a healthy query never trips it,
#: short enough that one hung query cannot outlive the hour the refresh runs in.
DEFAULT_QUERY_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    """The two reads a run needs, taken together on one session."""

    players: tuple[PoolPlayer, ...]
    #: `(data_run.isoformat(), str(player_id))`, exactly as `existing_keys` returns it.
    stored_keys: frozenset[tuple[str, str]]


class NewsGateway(Protocol):
    """The two database operations a roster news run needs, and nothing else."""

    def read(self, *, season: str, day: date) -> PoolSnapshot: ...

    def store(self, rows: Sequence[dict[str, str]], *, force: bool) -> int: ...


@dataclass(frozen=True, slots=True)
class Selection:
    """Who this run will ask about, and who it will not. Pure."""

    players: tuple[PoolPlayer, ...]
    #: Roster ids found in the season's pool.
    matched: int
    #: Matched players skipped because today's reading is already stored.
    resumed: int
    #: Roster ids with no row in the pool. Reported, never fatal.
    unmatched: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NewsRunResult:
    """What one roster news run did. `reason` is `None` exactly when it succeeded."""

    reason: str | None
    roster: int = 0
    matched: int = 0
    unmatched: tuple[int, ...] = ()
    resumed: int = 0
    #: Players this run set out to ask about. `selected - skipped` were actually asked.
    selected: int = 0
    #: Readings that came back.
    delivered: int = 0
    #: Rows handed to the database. Not rows inserted: without `force` the upsert does
    #: nothing on conflict, so a same-day re-run can hand over 30 and insert none.
    stored: int = 0
    failures: tuple[tuple[str, str], ...] = ()
    #: Players never asked, because the deadline or a failure run stopped the fan-out.
    skipped: int = 0
    #: Readings held and never stored. Non-zero means loss.
    unstored: int = 0
    #: `fetch_all`'s own wording. It cannot tell a deadline from a Ctrl-C and neither can
    #: this, so the text is passed along rather than re-interpreted.
    stopped_early: str | None = None
    rate_limited: bool = False
    usage: Usage | None = None
    #: Only on a dry run; a writing run stores and does not carry them back.
    rows: tuple[dict[str, str], ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.reason is None

    def summary(self) -> str:
        """One line of counts. Markup-free and carrying no agent-written text."""
        head = "ok" if self.ok else f"failed: {self.reason}"
        return (
            f"news {head} - roster {self.roster}, matched {self.matched}, "
            f"resumed {self.resumed}, asked {self.selected - self.skipped}, "
            f"delivered {self.delivered}, stored {self.stored}, "
            f"failed {len(self.failures)}, skipped {self.skipped}"
        )


def select_players(
    roster_ids: Collection[int], snapshot: PoolSnapshot, *, day: date, force: bool
) -> Selection:
    """Which pool players this run asks about. Pure.

    ⚠ **The join is `int` against `str`.** `PoolPlayer.id` is a string and a roster id is
    an `int`, so `player.id in roster_ids` matches nothing at all — and matches nothing
    *quietly*, reporting an empty roster every week. The roster is stringified once, here.
    """
    wanted = {str(pid) for pid in roster_ids}
    matched = [player for player in snapshot.players if player.id in wanted]
    found = {player.id for player in matched}
    unmatched = tuple(sorted(pid for pid in roster_ids if str(pid) not in found))
    if force:
        return Selection(tuple(matched), len(matched), 0, unmatched)
    fresh = tuple(
        player
        for player in matched
        if (day.isoformat(), player.id) not in snapshot.stored_keys
    )
    return Selection(fresh, len(matched), len(matched) - len(fresh), unmatched)


def timed_runner(runner: Runner, *, seconds: float) -> Runner:
    """`runner`, cancelled after `seconds` and reported as an ordinary failure.

    ⚠ `TimeoutError`, never `CancelledError`. On 3.11 `asyncio.TimeoutError` *is* the
    builtin `TimeoutError`; `CancelledError` is a `BaseException` and is how an outer
    cancellation reaches through, so catching it would swallow the caller's own shutdown.
    `fetch_all`'s own handler is `except Exception` for the same reason.
    """
    from fantabot.adapters.agent.runner import Outcome

    async def run(request: Any, schema: Any) -> Any:
        try:
            return await asyncio.wait_for(runner(request, schema), seconds)
        except TimeoutError:
            return Outcome(value=None, failure=f"no answer within {seconds:.0f}s")

    return run


async def fetch_roster_news(
    roster_ids: Collection[int],
    *,
    gateway: NewsGateway,
    reporter: Reporter,
    model: str,
    season: str,
    day: date,
    write: bool,
    runner: Runner | None = None,
    should_stop: Callable[[], bool] | None = None,
    force: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    flush_every: int = DEFAULT_FLUSH_EVERY,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    query_timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    backoff_seconds: float | None = None,
) -> NewsRunResult:
    """Query news for the lega's roster and store the readings. Returns a verdict.

    Never raises for an ordinary failure: a refusal, an empty roster and a run that stored
    nothing all come back as `reason`. What does propagate is what `fetch_all` propagates —
    an `AssertionError` or a `KeyboardInterrupt` is a bug or an operator, not news.
    """
    # Before the read, not after: an empty roster must not cost a multi-megabyte pool
    # query, and a test can only prove that with a gateway that refuses to be touched.
    if not roster_ids:
        return NewsRunResult(reason="no roster to query")

    snapshot = gateway.read(season=season, day=day)
    selection = select_players(roster_ids, snapshot, day=day, force=force)
    if selection.unmatched:
        reporter.print(
            f"[dim]{len(selection.unmatched)} roster id(s) are not in the {season} pool[/dim]"
        )
    if not selection.matched:
        return NewsRunResult(
            reason=f"no roster player is in the {season} pool",
            roster=len(roster_ids),
            unmatched=selection.unmatched,
        )
    if not selection.players:
        return NewsRunResult(
            reason=None,
            roster=len(roster_ids),
            matched=selection.matched,
            unmatched=selection.unmatched,
            resumed=selection.resumed,
        )

    from fantabot.adapters.agent.env import strip_dangerous_env
    from fantabot.application.news_fetcher import fetch_all

    strip_dangerous_env()

    sink: SentimentSink | None = None
    if write:
        # `SentimentSink`'s `on_error` exists so a store failure is said out loud; it used
        # to append the type name to a list nobody read, which said it out loud to nobody.
        # An exception's *class name* is safe to print — it is Python's, not the agent's,
        # so none of the markup this module keeps off the Reporter can reach it.
        sink = SentimentSink(
            lambda rows: gateway.store(rows, force=force),
            every=flush_every,
            on_error=lambda exc: reporter.print(
                f"[yellow]a batch of readings could not be stored ({type(exc).__name__}) "
                "— they are held for the next flush[/yellow]"
            ),
        )

    def on_result(progress: Any) -> None:
        row = progress.outcome.row
        if sink is not None and row is not None:
            sink.add(row)

    extra: dict[str, Any] = {}
    if runner is not None:
        extra["runner"] = timed_runner(runner, seconds=query_timeout)
    if backoff_seconds is not None:
        extra["backoff_seconds"] = backoff_seconds
    result = await fetch_all(
        selection.players,
        concurrency=concurrency,
        lookback_days=lookback_days,
        today=day,
        model=model,
        stagione=season,
        on_result=on_result,
        should_stop=should_stop,
        **extra,
    )

    if sink is not None:
        # Normally a no-op — the sink skips keys it has taken — and kept as the guarantee
        # that a bug in the incremental path cannot lose a run.
        sink.extend(result.rows)
        sink.drain()

    return NewsRunResult(
        reason=_verdict(result, sink),
        roster=len(roster_ids),
        matched=selection.matched,
        unmatched=selection.unmatched,
        resumed=selection.resumed,
        selected=len(selection.players),
        delivered=len(result.rows),
        stored=sink.stored if sink is not None else 0,
        failures=tuple(result.failures),
        skipped=result.skipped,
        unstored=sink.pending if sink is not None else 0,
        stopped_early=result.stopped_early,
        rate_limited=result.rate_limited,
        usage=result.usage,
        rows=() if write else tuple(result.rows),
    )


def _verdict(result: Any, sink: SentimentSink | None) -> str | None:
    """Why this run failed, or `None`. The one place that decides.

    A run that asked and got nothing back is a failure even though every individual player
    failed "routinely": the marker it writes is what tells the next hour not to try again,
    and writing it for a run that collected nothing is the one outcome worth refusing.
    """
    if sink is not None and sink.pending:
        return f"{sink.pending} reading(s) could not be stored"
    if not result.rows:
        return "no reading was collected"
    return None


class PostgresNewsGateway:
    """The real gateway. Persistence is imported inside the methods, so importing this
    module does not import sqlalchemy — the hourly `lineup submit` loads neither."""

    def read(self, *, season: str, day: date) -> PoolSnapshot:
        """The pool and the resume filter, on **one** session: they are the same question
        asked twice — what is this run going to query?"""
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.news_pool import load_pool
        from fantabot.adapters.persistence.repositories.sentiment import SentimentRepository

        with database_manager.get_session() as session:
            players = load_pool(session, season)
            keys = SentimentRepository(session).existing_keys(day)
        return PoolSnapshot(tuple(players), frozenset(keys))

    def store(self, rows: Sequence[dict[str, str]], *, force: bool) -> int:
        """A **fresh** session per flush. One held across a run that lasts minutes leaves a
        Postgres connection idle-in-transaction — `lega_sync`'s rule."""
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories.sentiment import SentimentRepository

        with database_manager.get_session() as session:
            return SentimentRepository(session).upsert_rows(list(rows), force=force)

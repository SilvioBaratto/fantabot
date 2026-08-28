"""Fan-out over the player pool.

523 players a week means a failing player is routine, not exceptional, so nothing
here lets one end the run. Three failure shapes are absorbed: an ``Outcome`` that
carries a reason (a bad subtype, a schema rejection), a raised exception (a
transport error), and a reply that validates but cannot be turned into a row (an
off-vocabulary Mantra code). All three land in ``failures`` and the run continues.

The runner is injected. That is what lets the whole fan-out — concurrency cap,
rate-limit backoff, failure isolation, ordering — be tested without a single
agent call. ``on_start`` and ``on_result`` are injected for the same reason, and
exist for the reason CLAUDE.md records about the other long-running command: *a
run with no end must speak while it runs*, because the summary it prints at exit
is the one thing an interrupted run never reaches. 548 players at two a minute
is nearly two hours, and until 2026-08-28 the only sign of life in it was the
subprocess count.

Rows are built at completion rather than after the last player, so ``on_result``
can hand one straight to a sink. The returned rows stay in **pool** order; only
the callbacks see completion order.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from ..agentkit.options import AgentRequest
from ..agentkit.runner import Outcome, Usage
from ..agentkit.runner import run as sdk_run
from .models import PlayerSentiment
from .pool import PoolPlayer
from .prompt import build_prompt
from .store import build_row

log = logging.getLogger(__name__)

Runner = Callable[[AgentRequest, type[PlayerSentiment]], Awaitable[Outcome[PlayerSentiment]]]
Sleeper = Callable[[float], Awaitable[None]]

ALLOWED_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")
MAX_TURNS = 12
DEFAULT_BACKOFF_SECONDS = 30.0

#: Consecutive failures, with no success between them, that end a run.
#:
#: A failing player is routine and must not stop anything. Every player
#: failing is not a player problem at all, and the reason text cannot tell
#: the two apart — on 2026-08-28 an exhausted Ollama quota returned HTTP 429
#: for every query from player 76 on, and each one arrived as the ordinary
#: ``agent returned no structured output``. Only the *shape* distinguishes
#: them, and ten in a row is not a shape any weekly pool produces.
DEFAULT_MAX_CONSECUTIVE_FAILURES = 10


@dataclass(frozen=True)
class PlayerOutcome:
    """One player's finished query, however it finished."""

    player: PoolPlayer
    row: dict[str, str] | None
    failure: str | None
    rate_limited: bool
    #: Never queried, because the run had already stopped. Not a failure: the
    #: resume filter must offer this player again, and a report that called
    #: 458 unasked players "failed" would describe the outage as a rout.
    skipped: bool = False
    #: What this query spent. Present even on a failure — a player can burn web
    #: searches before its answer is rejected — and empty for a skipped one.
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class Progress:
    """An outcome, plus where the run has got to. What ``on_result`` receives."""

    done: int
    total: int
    outcome: PlayerOutcome


@dataclass(frozen=True)
class FetchResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    rate_limited: bool = False
    #: Why the run ended early, or None if it ran to the end of the pool.
    stopped_early: str | None = None
    #: Players never queried because it stopped.
    skipped: int = 0
    #: Token spend for the whole run, folded from every player's usage.
    usage: Usage = field(default_factory=Usage)


StartHook = Callable[[PoolPlayer], None]
ResultHook = Callable[[Progress], None]


def total_usage(outcomes: Iterable[PlayerOutcome]) -> Usage:
    """Fold every finished player's usage into one figure.

    Failed and skipped players are included: a player can spend web searches before
    its answer is rejected, and that token cost is still part of the run's cost.
    """
    total = Usage()
    for outcome in outcomes:
        total = total + outcome.usage
    return total


def format_cost_line(usage: Usage) -> str:
    """The one stdout line ``news-fetch`` prints at the end of a run.

    The cache-read fraction is the reused share of all cacheable input (uncached
    input + cache writes + cache reads); a higher fraction is a cheaper run and the
    number Tasks 2-3 are trying to move. The dollar figure is hedged because the
    SDK's price table can report 0 for a custom Foundry model id, so the tokens and
    the fraction are the load-bearing numbers, not the estimate.
    """
    cacheable = usage.input_tokens + usage.cache_creation_tokens + usage.cache_read_tokens
    read_pct = round(100 * usage.cache_read_tokens / cacheable) if cacheable else 0
    cost = f"${usage.cost_usd:.4f}" if usage.cost_usd else "$0"
    return (
        f"tokens: {usage.input_tokens:,} in · {usage.output_tokens:,} out · "
        f"cache {read_pct}% read ({usage.cache_read_tokens:,}/{cacheable:,}) · "
        f"est ~{cost} (approx, may be 0 on a custom model)"
    )


async def fetch_all(
    players: Sequence[PoolPlayer],
    *,
    runner: Runner = sdk_run,
    concurrency: int = 4,
    lookback_days: int = 14,
    today: date,
    model: str,
    stagione: str,
    sleep: Sleeper = asyncio.sleep,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    on_start: StartHook | None = None,
    on_result: ResultHook | None = None,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    should_stop: Callable[[], bool] | None = None,
) -> FetchResult:
    semaphore = asyncio.Semaphore(concurrency)
    total = len(players)
    done = 0
    consecutive = 0
    stopped: str | None = None

    def _row(player: PoolPlayer, value: PlayerSentiment) -> dict[str, str]:
        return build_row(
            player=player,
            sentiment=value,
            data_run=today,
            giorni_lookback=lookback_days,
            stagione=stagione,
            modello=model,
        )

    async def one(player: PoolPlayer) -> PlayerOutcome:
        nonlocal done, consecutive, stopped
        async with semaphore:
            if stopped is None and should_stop is not None and should_stop():
                # An interrupt stops the run the same way an unanswering backend
                # does: by not asking anyone else. Cancelling a query already in
                # flight would throw away web searches that have been paid for,
                # so what is running finishes and is returned to be stored.
                stopped = "interrupted — no further player was queried"
            if stopped is not None:
                # Queued behind the wall. Return without querying and without a
                # progress line: the run has already said why it ended, and 458
                # more lines would bury it.
                return PlayerOutcome(player, None, None, False, skipped=True)
            if on_start is not None:
                # Inside the slot, so the announcement marks the query actually
                # starting rather than 548 names printed at once.
                on_start(player)
            request = AgentRequest(
                prompt=build_prompt(player, lookback_days, today),
                label=player.nome,
                model=model,
                allowed_tools=ALLOWED_TOOLS,
                max_turns=MAX_TURNS,
            )
            try:
                outcome = await runner(request, PlayerSentiment)
                if outcome.rate_limited:
                    # Back off inside the slot, so the pause actually throttles rather
                    # than letting the next player start immediately behind it.
                    await sleep(backoff_seconds)
                # Building is inside the guard, not after it. `ruolo_campo` is an
                # unconstrained list[str], so a model answering "Trequartista"
                # validates and then makes `build_row` raise `UnknownRoleCode` —
                # the third routine failure shape, and the only one that used to
                # escape `gather` and take the queue and the unflushed rows with it.
                result = PlayerOutcome(
                    player=player,
                    row=None if outcome.value is None else _row(player, outcome.value),
                    failure=outcome.failure,
                    rate_limited=outcome.rate_limited,
                    usage=outcome.usage,
                )
            except Exception as exc:
                log.warning("%s: query raised: %s", player.nome, exc)
                result = PlayerOutcome(player, None, f"{type(exc).__name__}: {exc}", False)

            # Counted for every shape of ending, including the raising one: a
            # progress line that skipped failures would stop short of the total
            # and read as a run that never finished.
            done += 1
            if on_result is not None:
                on_result(Progress(done=done, total=total, outcome=result))

            if result.row is None:
                consecutive += 1
                if max_consecutive_failures and consecutive >= max_consecutive_failures:
                    stopped = (
                        f"stopped after {consecutive} consecutive failures with no success "
                        f"between them, the last: {result.failure}"
                    )
            else:
                # Any success means the backend is alive and the failures were
                # about the players, which is what the run is built to absorb.
                consecutive = 0
            return result

    # gather preserves input order regardless of completion order — diffs, logs and
    # the resume index all depend on the output matching the pool's ordering. The
    # callbacks above see completion order; these rows must not.
    outcomes = list(await asyncio.gather(*(one(player) for player in players)))

    rows: list[dict[str, str]] = []
    failures: list[tuple[str, str]] = []
    rate_limited = False
    skipped = 0
    for outcome in outcomes:
        rate_limited = rate_limited or outcome.rate_limited
        if outcome.skipped:
            skipped += 1
            continue
        if outcome.row is None:
            failures.append(
                (outcome.player.nome, outcome.failure or "no value and no reason given")
            )
            continue
        rows.append(outcome.row)

    return FetchResult(
        rows=rows,
        failures=failures,
        rate_limited=rate_limited,
        stopped_early=stopped,
        skipped=skipped,
        usage=total_usage(outcomes),
    )

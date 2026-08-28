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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from ..agentkit.options import AgentRequest
from ..agentkit.runner import Outcome
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


@dataclass(frozen=True)
class PlayerOutcome:
    """One player's finished query, however it finished."""

    player: PoolPlayer
    row: dict[str, str] | None
    failure: str | None
    rate_limited: bool


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


StartHook = Callable[[PoolPlayer], None]
ResultHook = Callable[[Progress], None]


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
) -> FetchResult:
    semaphore = asyncio.Semaphore(concurrency)
    total = len(players)
    done = 0

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
        nonlocal done
        async with semaphore:
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
            return result

    # gather preserves input order regardless of completion order — diffs, logs and
    # the resume index all depend on the output matching the pool's ordering. The
    # callbacks above see completion order; these rows must not.
    outcomes = list(await asyncio.gather(*(one(player) for player in players)))

    rows: list[dict[str, str]] = []
    failures: list[tuple[str, str]] = []
    rate_limited = False
    for outcome in outcomes:
        rate_limited = rate_limited or outcome.rate_limited
        if outcome.row is None:
            failures.append(
                (outcome.player.nome, outcome.failure or "no value and no reason given")
            )
            continue
        rows.append(outcome.row)

    return FetchResult(rows=rows, failures=failures, rate_limited=rate_limited)

"""Fan-out over the player pool.

523 players a week means a failing player is routine, not exceptional, so nothing
here lets one end the run. Two failure shapes are absorbed: an ``Outcome`` that
carries a reason (a bad subtype, a schema rejection) and a raised exception (a
transport error). Both land in ``failures`` and the run continues.

The runner is injected. That is what lets the whole fan-out — concurrency cap,
rate-limit backoff, failure isolation, ordering — be tested without a single
agent call.
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
class FetchResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    rate_limited: bool = False


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
) -> FetchResult:
    semaphore = asyncio.Semaphore(concurrency)
    outcomes: list[tuple[PoolPlayer, PlayerSentiment | None, str | None, bool]] = []

    async def one(
        player: PoolPlayer,
    ) -> tuple[PoolPlayer, PlayerSentiment | None, str | None, bool]:
        async with semaphore:
            request = AgentRequest(
                prompt=build_prompt(player, lookback_days, today),
                label=player.nome,
                model=model,
                allowed_tools=ALLOWED_TOOLS,
                max_turns=MAX_TURNS,
            )
            try:
                outcome = await runner(request, PlayerSentiment)
            except Exception as exc:
                log.warning("%s: query raised: %s", player.nome, exc)
                return player, None, f"{type(exc).__name__}: {exc}", False

            if outcome.rate_limited:
                # Back off inside the slot, so the pause actually throttles rather
                # than letting the next player start immediately behind it.
                await sleep(backoff_seconds)
            return player, outcome.value, outcome.failure, outcome.rate_limited

    # gather preserves input order regardless of completion order — diffs, logs and
    # the resume index all depend on the output matching the pool's ordering.
    outcomes = list(await asyncio.gather(*(one(player) for player in players)))

    rows: list[dict[str, str]] = []
    failures: list[tuple[str, str]] = []
    rate_limited = False
    for player, value, failure, limited in outcomes:
        rate_limited = rate_limited or limited
        if value is None:
            failures.append((player.nome, failure or "no value and no reason given"))
            continue
        rows.append(
            build_row(
                player=player,
                sentiment=value,
                data_run=today,
                giorni_lookback=lookback_days,
                stagione=stagione,
                modello=model,
            )
        )

    return FetchResult(rows=rows, failures=failures, rate_limited=rate_limited)

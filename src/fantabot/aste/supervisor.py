"""Many auctions at once: starting watchers, restarting them, and saying so.

**The pool bound is ours, not theirs.** Spike S1 opened 207 concurrent SSE
streams — the entire live population on 2026-08-27, across 20 shards — and
Firebase refused none of them, dropped none, and delivered 1,053 frames in 20
seconds. So the default is set above a whole live population: throttling for a
limit nobody imposed would cost coverage for nothing.

Two outcomes are treated differently, and conflating them is what cost the
poller data twice:

**Ended is final.** The node was deleted; restarting a watcher on it would poll
a dead address all evening.

**Unreachable is ours.** The auction may still be live and we simply could not
hold a connection, so it is retried — but not forever, or one broken shard
occupies a slot until morning.

**A crash is survivable and counted.** On 2026-08-26 a ``JSONDecodeError`` from a
gateway error page killed one watcher in silence while the heartbeat kept
reporting health. Here it restarts, and ``crashed`` goes into the report — a
number that only ever rises is a signal even when nothing else looks wrong.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fantabot.aste.registry import AuctionConfig
from fantabot.aste.stream import Outcome

#: Above a whole live population, because S1 found no server-side cap at 207.
DEFAULT_POOL = 250

#: How many times an *unreachable* auction is retried before its slot is freed.
DEFAULT_MAX_RESTARTS = 5

Watch = Callable[..., Awaitable[Outcome]]
OnState = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class Report:
    """What a run did, per outcome.

    ``expected`` is carried so a caller can print ``live / expected`` rather than
    ``live``. A heartbeat that only says what is running cannot tell a quiet
    evening from half the watchers having died — which is precisely the failure
    that went unnoticed for an hour on 2026-08-26.
    """

    expected: int = 0
    ended: int = 0
    unreachable: int = 0
    crashed: int = 0
    live: int = 0
    _seen: set[str] = field(default_factory=set)

    def summary(self) -> str:
        return (
            f"{self.live}/{self.expected} live · ended {self.ended} · "
            f"unreachable {self.unreachable} · crashed {self.crashed}"
        )


class Supervisor:
    """Runs one watcher per auction, within a bounded pool."""

    def __init__(
        self,
        *,
        watch: Watch,
        on_state: OnState,
        sleep: Callable[[float], Awaitable[None]],
        pool: int = DEFAULT_POOL,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        retry_delay: float = 5.0,
    ) -> None:
        self._watch = watch
        self._on_state = on_state
        self._sleep = sleep
        self._pool = max(1, pool)
        self._max_restarts = max(1, max_restarts)
        self._retry_delay = retry_delay

    async def run(self, configs: list[AuctionConfig]) -> Report:
        """Follow every auction until each ends or gives up."""
        report = Report(expected=len(configs))
        semaphore = asyncio.Semaphore(self._pool)

        async def supervise(config: AuctionConfig) -> None:
            attempts = 0
            while attempts < self._max_restarts:
                attempts += 1
                async with semaphore:
                    report.live += 1
                    try:
                        outcome = await self._watch(config, on_state=self._on_state)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        report.crashed += 1
                        outcome = None
                    finally:
                        report.live -= 1

                if outcome is Outcome.ENDED:
                    report.ended += 1
                    return
                if attempts >= self._max_restarts:
                    break
                await self._sleep(self._retry_delay)

            report.unreachable += 1

        await asyncio.gather(*(supervise(config) for config in configs))
        return report

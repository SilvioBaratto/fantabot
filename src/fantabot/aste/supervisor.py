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
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from fantabot.aste.registry import AuctionConfig
from fantabot.aste.stream import Outcome, SinkFailed

#: Above a whole live population, because S1 found no server-side cap at 207.
DEFAULT_POOL = 250

#: How many times an *unreachable* auction is retried before its slot is freed.
DEFAULT_MAX_RESTARTS = 5

#: A watcher is handed its config and nothing else. Where its states go is the
#: caller's business: the landing zone needs the auction id alongside every
#: state, and one callback shared across watchers cannot supply it — so the
#: sink is closed over in the `watch` callable, where the id is in scope.
Watch = Callable[[AuctionConfig], Awaitable[Outcome]]

#: Re-reads the population. Raising is survivable; see `run`.
Reload = Callable[[], list[AuctionConfig]]


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
    adopted: int = 0
    reload_failures: int = 0

    def summary(self) -> str:
        line = (
            f"{self.live}/{self.expected} live · ended {self.ended} · "
            f"unreachable {self.unreachable} · crashed {self.crashed}"
        )
        if self.adopted or self.reload_failures:
            line += f" · adopted {self.adopted} · unreadable seed {self.reload_failures}"
        return line


class Supervisor:
    """Runs one watcher per auction, within a bounded pool."""

    def __init__(
        self,
        *,
        watch: Watch,
        sleep: Callable[[float], Awaitable[None]],
        pool: int = DEFAULT_POOL,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        retry_delay: float = 5.0,
    ) -> None:
        self._watch = watch
        self._sleep = sleep
        self._pool = max(1, pool)
        self._max_restarts = max(1, max_restarts)
        self._retry_delay = retry_delay

    async def run(
        self,
        configs: list[AuctionConfig],
        *,
        reload: Reload | None = None,
        reload_every: float = 60.0,
        reloads: int | None = None,
    ) -> Report:
        """Follow every auction until each ends or gives up.

        With ``reload``, the population is re-read every ``reload_every``
        seconds and any auction not already followed gets a watcher. Without it
        the seed is read once, which is what the collector did: `aste-scan`
        rewrites that file whenever it runs, and on a live evening it finds
        rooms that did not exist an hour earlier — so every asta opening
        mid-evening was lost, with nothing in the report saying so.

        ``reloads`` bounds the number of cycles. ``None`` means until the
        process is interrupted, which is how an evening is collected.
        """
        report = Report()
        semaphore = asyncio.Semaphore(self._pool)
        started: set[str] = set()
        tasks: set[asyncio.Task[None]] = set()

        async def supervise(config: AuctionConfig) -> None:
            attempts = 0
            while attempts < self._max_restarts:
                attempts += 1
                async with semaphore:
                    report.live += 1
                    try:
                        outcome = await self._watch(config)
                    except asyncio.CancelledError:
                        raise
                    except SinkFailed:
                        # Not a crash to survive. A failing sink means writes are
                        # not landing, so retrying turns a full disk into a loop
                        # that reconnects for ever and stores nothing — which is
                        # what stream.py raises this type to prevent. Without
                        # this line the CLI's own `except SinkFailed` was
                        # unreachable and the command exited 0 having written
                        # no states at all.
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

        def adopt(batch: Iterable[AuctionConfig]) -> None:
            """Start a watcher for every auction not already followed."""
            for config in batch:
                if config.auction_id in started:
                    continue
                started.add(config.auction_id)
                report.expected += 1
                tasks.add(asyncio.create_task(supervise(config)))

        def reap() -> None:
            """Surface a finished watcher's exception now, not at the end.

            With no reload loop the final ``gather`` raised it immediately. With
            one, a ``SinkFailed`` would sit inside a completed task until the
            loop ended — and the loop is meant to run all evening, so a full
            disk would go unnoticed for exactly as long as it matters.
            """
            for task in [one for one in tasks if one.done()]:
                tasks.discard(task)
                if (failure := task.exception()) is not None:
                    for other in tasks:
                        other.cancel()
                    raise failure

        adopt(configs)
        if reload is None:
            await asyncio.gather(*tasks)
            return report

        cycles = 0
        while reloads is None or cycles < reloads:
            cycles += 1
            await self._sleep(reload_every)
            reap()
            try:
                batch = reload()
            except Exception:
                # A half-written seed costs one cycle. Letting it out would kill
                # every watcher already running, which is the opposite of what a
                # reload is for.
                report.reload_failures += 1
                continue
            before = len(started)
            adopt(batch)
            report.adopted += len(started) - before

        await asyncio.gather(*tasks)
        return report

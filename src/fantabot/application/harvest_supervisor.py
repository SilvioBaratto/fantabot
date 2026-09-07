"""Many auctions at once: starting watchers, restarting them, and saying so.

**The pool bound is ours, not theirs.** Spike S1 opened 207 concurrent SSE
streams and Firebase refused none of them, dropped none, and delivered 1,053
frames in 20 seconds. Throttling for a limit nobody imposes costs coverage for
nothing.

**And a pool below the population is silent starvation.** S1's 207 was read as
"a whole live population" and the default set to 250 above it. On the evening of
2026-08-27 the population was 649, and a live run put exactly 250 distinct
auctions in the landing zone: the other 145 waited on the semaphore, and since a
watcher on a live evening does not finish, no permit was ever freed. They never
connected. The run said `following 395 auction(s)` and then nothing for hours.
The default is now above the largest population measured, and — because that
number will be wrong again — `run` reports `live / expected` on every cycle, so
the shortfall is a line in the log rather than something to infer from a row
count the next morning.

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
from contextlib import suppress
from dataclasses import dataclass

from fantabot.adapters.http.harvest.stream import Outcome, SinkFailed
from fantabot.domain.harvest.registry import AuctionConfig

#: Above the largest population measured: 649 auctions live at 21:57 on
#: 2026-08-27. Not a law — next season will exceed it — which is why a run that
#: hits the bound says so instead of quietly following the first N.
DEFAULT_POOL = 1000

#: How many times an *unreachable* auction is retried before its slot is freed.
DEFAULT_MAX_RESTARTS = 5

#: A watcher is handed its config and nothing else. Where its states go is the
#: caller's business: the landing zone needs the auction id alongside every
#: state, and one callback shared across watchers cannot supply it — so the
#: sink is closed over in the `watch` callable, where the id is in scope.
Watch = Callable[[AuctionConfig], Awaitable[Outcome]]

#: Re-reads the population. Raising is survivable; see `run`.
Reload = Callable[[], list[AuctionConfig]]

#: Called with the running report each reload cycle. The only thing that speaks
#: during a run that has no end.
Heartbeat = Callable[["Report"], None]

#: Resolves when someone outside the process asks this run to stop, with the stage they
#: asked for. `adapters/files/stopflag.wait_for_stop` is the one implementation; it is a
#: parameter because the supervisor may not read a file, and because a test that waited
#: on a real one would be a race.
Stop = Callable[[], Awaitable[str]]


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
    #: The stage that ended this run, or `None` if nothing asked it to stop. Not a bool:
    #: the two stages mean different things to different commands, and a caller that has
    #: to say *why* it stopped cannot recover that from `True`.
    stopped: str | None = None

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
        heartbeat: Heartbeat | None = None,
        stop: Stop | None = None,
    ) -> Report:
        """Follow every auction until each ends or gives up.

        With ``reload``, the population is re-read every ``reload_every``
        seconds and any auction not already followed gets a watcher. Without it
        the seed is read once, which is what the collector did: `harvest scan`
        rewrites that file whenever it runs, and on a live evening it finds
        rooms that did not exist an hour earlier — so every asta opening
        mid-evening was lost, with nothing in the report saying so.

        ``reloads`` bounds the number of cycles. ``None`` means until the
        process is interrupted, which is how an evening is collected.

        ``heartbeat`` is handed the report each cycle. Without it a reloading
        run says nothing between "following N auctions" and the summary it only
        prints if it ever stops — which is how 145 starved watchers went
        unnoticed for an evening.

        ``stop`` is the cooperative shutdown: an awaitable that resolves when someone
        outside this process asks the run to end, with the stage they asked for. It is
        raced against the watchers, and when it wins every watcher is cancelled and
        ``report.stopped`` carries the stage. Without it the run ends only when the
        auctions do, which is what a terminal run relies on ``KeyboardInterrupt`` for —
        and what has no equivalent on Windows, where the only signal that reaches a child
        kills it before its own shutdown runs. See ``adapters/files/stopflag``.
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
            failure: BaseException | None = None
            for task in [one for one in tasks if one.done()]:
                tasks.discard(task)
                # Every finished task is asked, not just up to the first bad
                # one: an exception nobody retrieves is logged by asyncio at
                # collection time, and a burst of those buries the one line
                # that says why the run stopped.
                if (raised := task.exception()) is not None and failure is None:
                    failure = raised
            if failure is not None:
                for other in tasks:
                    other.cancel()
                raise failure

        async def follow() -> None:
            """Every watcher, to the end of the run. Both shapes of it."""
            if reload is None:
                await asyncio.gather(*tasks)
                return

            cycles = 0
            while reloads is None or cycles < reloads:
                cycles += 1
                await self._sleep(reload_every)
                if heartbeat is not None:
                    heartbeat(report)
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

        adopt(configs)
        if stop is None:
            await follow()
            return report

        # The race lives here rather than around `run` because *this* is what owns
        # `tasks`. A racer outside would have to reach in to cancel the watchers, and the
        # one thing a stop must not do is return while they are still streaming — the
        # process would then hold the role lock with nobody watching it.
        work = asyncio.create_task(follow())
        # `ensure_future`, not `create_task`: `Stop` is declared as returning an
        # `Awaitable` so a caller may hand over anything awaitable, and `create_task`
        # accepts only a coroutine.
        waiting = asyncio.ensure_future(stop())
        await asyncio.wait({work, waiting}, return_when=asyncio.FIRST_COMPLETED)

        if work.done():
            # The ordinary end: every auction finished. Cancel the waiter, or the process
            # sits on a poll that will never fire.
            waiting.cancel()
            with suppress(asyncio.CancelledError):
                await waiting
            await work  # re-raises whatever `follow` raised; `SinkFailed` is the one
            return report

        report.stopped = waiting.result()
        for task in tasks:
            task.cancel()
        work.cancel()
        with suppress(asyncio.CancelledError):
            await work
        # `return_exceptions`: a watcher cancelled mid-flight is not a failure to report,
        # and an unretrieved one is logged by asyncio at collection time — a burst of
        # those buries the line that says the run was stopped.
        settled = await asyncio.gather(*tasks, return_exceptions=True)

        # But retrieving them is not the same as discarding them. `reap()` runs only at a
        # reload-cycle boundary, so a `SinkFailed` sits in a completed watcher for up to
        # `reload_every` seconds; a stop resolving inside that window used to collect it
        # here as a *value* and drop it, and the run returned `stopped='exit'` over a full
        # disk. Under SIGINT the same interleaving at least left asyncio's "Task exception
        # was never retrieved" in the job log, so swallowing it here made a failure
        # quieter than the mechanism this replaced.
        #
        # **The sink failure outranks the stop.** `SinkFailed` means writes are not
        # landing — it exists because retrying turns a full disk into a loop that
        # reconnects for ever and stores nothing. "You asked me to stop" is the less
        # urgent of the two things to say, and `report.stopped` is lost with the raise
        # deliberately: the operator who clicked Stop already knows they clicked it.
        #
        # `CancelledError` is excluded because it is how a stop *works*, not a failure.
        failure = next(
            (
                outcome
                for outcome in settled
                if isinstance(outcome, BaseException)
                and not isinstance(outcome, asyncio.CancelledError)
            ),
            None,
        )
        if failure is not None:
            raise failure
        return report

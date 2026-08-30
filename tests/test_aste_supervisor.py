"""The supervisor: many auctions at once, and noticing when one stops.

Everything is driven by injected fakes — no socket, no clock. What is pinned is
the behaviour the poller got wrong twice: a watcher that dies must come back,
and an auction that ends must not.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from typing import Any

import pytest

from fantabot.aste.registry import AuctionConfig
from fantabot.aste.stream import Outcome, SinkFailed
from fantabot.aste.supervisor import DEFAULT_POOL, Report, Supervisor


def _configs(n: int) -> list[AuctionConfig]:
    return [
        AuctionConfig(auction_id=f"a-{i}", db_shard="18", asta_type="mantra") for i in range(n)
    ]


async def _no_sleep(_seconds: float) -> None:
    # Yields once rather than returning flat: a coroutine that never awaits
    # anything gives the loop no chance to start the watchers spawned before it,
    # so a reload cycle would see a population that has not moved yet.
    await asyncio.sleep(0)


def _run(supervisor: Supervisor, configs: list[AuctionConfig]) -> Any:
    return asyncio.run(supervisor.run(configs))


def test_every_auction_gets_a_watcher() -> None:
    seen: list[str] = []

    async def watch(config: AuctionConfig, **_k: Any) -> Outcome:
        seen.append(config.auction_id)
        return Outcome.ENDED

    _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(5))
    assert sorted(seen) == [f"a-{i}" for i in range(5)]


def test_an_auction_that_ended_is_not_restarted() -> None:
    """The poller could not tell an ending from a drop and gave up on both. Here
    an ending is an observed fact, so restarting one would poll a dead node
    forever."""
    counts: dict[str, int] = {}

    async def watch(config: AuctionConfig, **_k: Any) -> Outcome:
        counts[config.auction_id] = counts.get(config.auction_id, 0) + 1
        return Outcome.ENDED

    _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(3))
    assert set(counts.values()) == {1}


def test_a_watcher_that_raises_is_restarted() -> None:
    """On 2026-08-26 a JSONDecodeError killed one watcher silently while the
    heartbeat kept reporting health. A crash must be survivable and visible."""
    attempts: dict[str, int] = {}

    async def watch(config: AuctionConfig, **_k: Any) -> Outcome:
        n = attempts.get(config.auction_id, 0) + 1
        attempts[config.auction_id] = n
        if n == 1:
            raise RuntimeError("boom")
        return Outcome.ENDED

    supervisor = Supervisor(watch=watch, sleep=_no_sleep)
    report = _run(supervisor, _configs(2))
    assert attempts == {"a-0": 2, "a-1": 2}
    assert report.crashed == 2, "a crash must be counted, not just survived"


def test_an_unreachable_auction_is_retried_then_given_up_on() -> None:
    """Unreachable is our failure, not the auction's ending. It is retried — but
    not forever, or one dead shard occupies a slot all evening."""
    attempts: dict[str, int] = {}

    async def watch(config: AuctionConfig, **_k: Any) -> Outcome:
        attempts[config.auction_id] = attempts.get(config.auction_id, 0) + 1
        return Outcome.UNREACHABLE

    supervisor = Supervisor(watch=watch, sleep=_no_sleep, max_restarts=3)
    report = _run(supervisor, _configs(1))
    assert attempts["a-0"] == 3
    assert report.unreachable == 1


def test_the_pool_bounds_how_many_run_at_once() -> None:
    """S1 found no server-side cap at 207 concurrent streams — the whole live
    population. The bound here is our own sanity, not theirs."""
    concurrent = 0
    peak = 0

    async def watch(_config: AuctionConfig, **_k: Any) -> Outcome:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        concurrent -= 1
        return Outcome.ENDED

    _run(Supervisor(watch=watch, sleep=_no_sleep, pool=4), _configs(20))
    assert peak <= 4


def test_the_default_pool_covers_a_whole_live_population() -> None:
    """207 was the entire live list on 2026-08-27, and it did not refuse. A
    default below that would throttle for a limit nobody imposed."""
    assert DEFAULT_POOL >= 207


def test_the_report_says_live_over_expected_not_just_live() -> None:
    """A heartbeat that only prints what is running cannot distinguish a quiet
    evening from half the watchers having died."""
    async def watch(_config: AuctionConfig, **_k: Any) -> Outcome:
        return Outcome.ENDED

    report = _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(6))
    assert report.expected == 6
    assert report.ended == 6
    assert "6" in report.summary() and "/" in report.summary()


@pytest.mark.parametrize("count", [0, 1])
def test_an_empty_or_single_population_is_handled(count: int) -> None:
    async def watch(_config: AuctionConfig, **_k: Any) -> Outcome:
        return Outcome.ENDED

    report = _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(count))
    assert report.expected == count


def test_a_sink_failure_escapes_instead_of_being_retried() -> None:
    """`watch_auction` wraps a sink error in `SinkFailed` and re-raises it past
    its own reconnect loop, and `harvest collect` has an `except SinkFailed` that
    exits 1. The supervisor sat between them and its `except Exception` caught it
    first — so the CLI handler was unreachable on every path.

    Consequence measured: on a full disk, every watcher retried its limit, the
    run printed `crashed 5 · 0 states written`, and the command exited **0**.
    Cron would have been told the evening succeeded.
    """
    from fantabot.aste.stream import SinkFailed

    async def watch(_config: AuctionConfig, **_k: Any) -> Outcome:
        raise SinkFailed("No space left on device")

    supervisor = Supervisor(watch=watch, sleep=_no_sleep)
    with pytest.raises(SinkFailed, match="No space left"):
        _run(supervisor, _configs(3))


def test_an_ordinary_crash_is_still_survived() -> None:
    """The broad catch exists for a reason — a JSONDecodeError from a gateway
    page must not end the run. Narrowing it for SinkFailed must not narrow it
    for everything."""
    attempts: dict[str, int] = {}

    async def watch(config: AuctionConfig, **_k: Any) -> Outcome:
        n = attempts.get(config.auction_id, 0) + 1
        attempts[config.auction_id] = n
        if n == 1:
            raise ValueError("Expecting value: line 1 column 1")
        return Outcome.ENDED

    report = _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(1))
    assert report.ended == 1 and report.crashed == 1


def test_the_supervisor_does_not_own_a_state_callback() -> None:
    """`on_state` was a constructor parameter the supervisor could not use.

    States have to be written *per auction* — the landing zone needs the id
    alongside the state — and one callback shared by every watcher cannot supply
    it. So the only caller closed `zone.write(config.auction_id, ...)` into its
    own `watch` and absorbed the supervisor's copy with `**_unused`, passing
    `lambda _s: None` to satisfy a parameter that did nothing. The `watch`
    callable already carries the sink; the supervisor never needed a second one.
    """
    assert "on_state" not in inspect.signature(Supervisor.__init__).parameters
    watch_type = inspect.signature(Supervisor.__init__).parameters["watch"]
    assert watch_type.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_watcher_is_called_with_the_config_alone() -> None:
    seen: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def watch(*args: object, **kwargs: object) -> Outcome:
        seen.append((args, kwargs))
        return Outcome.ENDED

    _run(Supervisor(watch=watch, sleep=_no_sleep), _configs(1))
    assert seen == [((_configs(1)[0],), {})], (
        "the supervisor should hand a watcher its config and nothing else"
    )


def test_the_report_carries_no_field_nothing_reads() -> None:
    """`Report._seen` was a set that was never added to and never queried."""
    assert not [f for f in dataclasses.fields(Report) if f.name.startswith("_")]


class TestAdoptingNewAuctions:
    """An asta that opens after the collector started was never followed.

    `harvest collect --seed` read the file once. `harvest scan` writes it again every
    time it runs, and on a live evening it finds rooms that did not exist an
    hour earlier — 207 were open at once on 2026-08-27. The retired poller
    re-read its seed each cycle; the supervisor that replaced it did not, so
    every auction opening mid-evening was lost with nothing saying so.
    """

    def test_an_auction_added_to_the_seed_gets_a_watcher(self) -> None:
        seen: list[str] = []
        batches = [_configs(1), _configs(3)]

        async def watch(config: AuctionConfig) -> Outcome:
            seen.append(config.auction_id)
            return Outcome.ENDED

        report = _run_reloading(watch, batches)
        assert sorted(seen) == ["a-0", "a-1", "a-2"]
        assert report.expected == 3, "the denominator has to grow with the population"

    def test_an_auction_already_followed_is_not_followed_twice(self) -> None:
        seen: list[str] = []

        async def watch(config: AuctionConfig) -> Outcome:
            seen.append(config.auction_id)
            return Outcome.ENDED

        _run_reloading(watch, [_configs(2), _configs(2), _configs(2)])
        assert sorted(seen) == ["a-0", "a-1"], "a reload is a diff, not a restart"

    def test_an_unreadable_seed_is_skipped_rather_than_fatal(self) -> None:
        """`harvest scan` rewrites the file the collector is reading.

        Catching a half-written seed must cost one reload, not the evening's
        collection — every watcher already running would die with it.
        """
        seen: list[str] = []
        calls: list[int] = []

        async def watch(config: AuctionConfig) -> Outcome:
            seen.append(config.auction_id)
            return Outcome.ENDED

        def reload() -> list[AuctionConfig]:
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return _configs(3)

        report = asyncio.run(
            Supervisor(watch=watch, sleep=_no_sleep).run(
                _configs(2), reload=reload, reload_every=0.0, reloads=2
            )
        )
        assert sorted(seen) == ["a-0", "a-1", "a-2"]
        assert report.reload_failures == 1


def _run_reloading(watch: Any, batches: list[list[AuctionConfig]]) -> Any:
    """Run with a reload callable that yields each batch in turn."""
    remaining = list(batches[1:])

    def reload() -> list[AuctionConfig]:
        return remaining.pop(0) if remaining else batches[-1]

    return asyncio.run(
        Supervisor(watch=watch, sleep=_no_sleep).run(
            batches[0], reload=reload, reload_every=0.0, reloads=len(batches) - 1
        )
    )


def test_a_failing_sink_stops_a_reloading_run_too() -> None:
    """Watchers became tasks so new ones could join mid-run; that hid failures.

    With a single `gather`, a `SinkFailed` came out at once. A completed task
    holds its exception until someone asks, and the reload loop is meant to run
    all evening — so a full disk would have gone unnoticed for exactly as long
    as it matters, with the collector reconnecting and storing nothing.
    """
    started: list[str] = []

    async def watch(config: AuctionConfig) -> Outcome:
        started.append(config.auction_id)
        raise SinkFailed("no space left on device")

    with pytest.raises(SinkFailed):
        asyncio.run(
            Supervisor(watch=watch, sleep=_no_sleep).run(
                _configs(3), reload=lambda: _configs(50), reload_every=0.0, reloads=100
            )
        )
    assert len(started) < 50, "the run must stop, not adopt another fifty auctions"


class TestTheShortfallIsVisible:
    """A pool smaller than the population is silent, and that cost an evening.

    Measured 2026-08-27 21:57: 395 auctions in the seed, `DEFAULT_POOL` at 250,
    and exactly 250 distinct auction ids in the landing zone. The other 145 sat
    behind the semaphore, and because a watcher on a live evening does not
    finish, no permit was ever freed — they never connected at all. The run
    printed `following 395 auction(s)` and then nothing for the rest of the
    night. Restarting with `--pool 800` took the landing zone to 649 distinct
    auctions inside a minute.

    It also made the seed reload inert: an adopted auction joined the back of a
    queue that never moves.
    """

    def test_the_default_pool_is_above_the_population_that_has_been_seen(self) -> None:
        """649 live at once on 2026-08-27. A default under that starves by design."""
        assert DEFAULT_POOL >= 649

    def test_live_against_expected_is_reported_every_cycle(self) -> None:
        """`Report.summary()` was printed once, at the end — and with a reload
        there is no end. The one number that would have shown 250/395 was never
        reached."""
        beats: list[str] = []
        held = asyncio.Event()

        async def watch(config: AuctionConfig) -> Outcome:
            await held.wait()
            return Outcome.ENDED

        async def scenario() -> None:
            supervisor = Supervisor(watch=watch, sleep=_no_sleep, pool=2)
            task = asyncio.create_task(
                supervisor.run(
                    _configs(5),
                    reload=lambda: _configs(5),
                    reload_every=0.0,
                    reloads=3,
                    heartbeat=lambda report: beats.append(report.summary()),
                )
            )
            for _ in range(20):
                await asyncio.sleep(0)
            held.set()
            await task

        asyncio.run(scenario())
        assert beats, "a run that never ends must report while it runs"
        assert "2/5 live" in beats[0], (
            f"the shortfall has to be in the line itself, not inferred: {beats[0]}"
        )

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
from fantabot.aste.stream import Outcome
from fantabot.aste.supervisor import DEFAULT_POOL, Report, Supervisor


def _configs(n: int) -> list[AuctionConfig]:
    return [
        AuctionConfig(auction_id=f"a-{i}", db_shard="18", asta_type="mantra") for i in range(n)
    ]


async def _no_sleep(_seconds: float) -> None:
    return None


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
    its own reconnect loop, and `aste-collect` has an `except SinkFailed` that
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

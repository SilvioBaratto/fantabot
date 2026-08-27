"""One auction's subscription, driven by a fake transport.

No socket opens here — the autouse guard in conftest would fail the test if one
did. What is being checked is the part that cost the poller data: telling an
auction that ended from a connection that dropped, and surviving a body that is
not JSON.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

import pytest

from fantabot.aste.stream import Outcome, SinkFailed, watch_auction

AUCTION, SHARD = "abc-123", "18"

PUT = 'event: put\ndata: {"path":"/","data":{"price":1,"last_update":10}}\n\n'
RAISE = 'event: patch\ndata: {"path":"/","data":{"price":7,"last_update":11}}\n\n'
GONE = 'event: put\ndata: {"path":"/","data":null}\n\n'
GARBAGE = "event: patch\ndata: <html>502 Bad Gateway</html>\n\n"


def _transport(*attempts: Iterable[str] | type[Exception]):
    """Yields one scripted connection per reconnect attempt."""
    scripted = list(attempts)

    async def open_stream(url: str) -> AsyncIterator[str]:
        if not scripted:
            raise AssertionError("the watcher reconnected more times than scripted")
        current = scripted.pop(0)
        if isinstance(current, type) and issubclass(current, Exception):
            raise current("transport failed")
        for chunk in current:
            yield chunk

    return open_stream


async def _no_sleep(_seconds: float) -> None:
    return None


def _run(**kwargs) -> tuple[Outcome, list[dict]]:
    seen: list[dict] = []

    async def record(state: dict) -> None:
        seen.append(dict(state))

    outcome = asyncio.run(
        watch_auction(AUCTION, SHARD, on_state=record, sleep=_no_sleep, **kwargs)
    )
    return outcome, seen


def test_states_are_reported_as_frames_arrive() -> None:
    outcome, seen = _run(open_stream=_transport([PUT, RAISE, GONE]))
    assert outcome is Outcome.ENDED
    assert [s.get("price") for s in seen] == [1, 7]


def test_a_null_put_means_the_auction_ended() -> None:
    """The node is deleted when the room closes. That is a clean end signal, and
    it replaces the poller's guess of forty consecutive empty reads."""
    outcome, _ = _run(open_stream=_transport([PUT, GONE]))
    assert outcome is Outcome.ENDED


def test_a_dropped_connection_is_retried_not_treated_as_an_ending() -> None:
    """Conflating the two silently ends a watch — the bug the poller shipped
    with, and the reason an auction can go dark without anyone noticing."""
    outcome, seen = _run(open_stream=_transport(ConnectionError, [PUT, GONE]))
    assert outcome is Outcome.ENDED
    assert [s.get("price") for s in seen] == [1]


def test_reconnects_are_bounded_so_a_dead_shard_does_not_spin_forever() -> None:
    outcome, _ = _run(
        open_stream=_transport(*[ConnectionError] * 3),
        max_attempts=3,
    )
    assert outcome is Outcome.UNREACHABLE


def test_a_body_that_is_not_json_does_not_end_the_watch() -> None:
    """A gateway error page raising JSONDecodeError is what silently killed a
    watcher on 2026-08-26. It must be counted and stepped over."""
    outcome, seen = _run(open_stream=_transport([PUT, GARBAGE, RAISE, GONE]))
    assert outcome is Outcome.ENDED
    assert [s.get("price") for s in seen] == [1, 7]


def test_backoff_grows_and_is_jittered() -> None:
    """Fixed delays make every watcher retry in lockstep after a shared outage."""
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    asyncio.run(
        watch_auction(
            AUCTION,
            SHARD,
            on_state=lambda _state: None,
            sleep=record_sleep,
            open_stream=_transport(*[ConnectionError] * 4),
            max_attempts=4,
            jitter=lambda: 0.5,
        )
    )
    assert waits == sorted(waits), "backoff must not shrink"
    assert waits[-1] > waits[0], "backoff must actually grow"


def test_the_url_names_the_right_shard() -> None:
    asked: list[str] = []

    async def open_stream(url: str) -> AsyncIterator[str]:
        asked.append(url)
        for chunk in (PUT, GONE):
            yield chunk

    asyncio.run(
        watch_auction(AUCTION, SHARD, on_state=lambda _s: None, sleep=_no_sleep,
                      open_stream=open_stream)
    )
    assert asked == [f"https://fantalab-{SHARD}.europe-west1.firebasedatabase.app"
                     f"/auction/{AUCTION}.json"]


@pytest.mark.parametrize("chunking", [1, 3, 17])
def test_transport_chunk_size_does_not_change_the_result(chunking: int) -> None:
    stream = PUT + RAISE + GONE
    pieces = [stream[i : i + chunking] for i in range(0, len(stream), chunking)]
    outcome, seen = _run(open_stream=_transport(pieces))
    assert outcome is Outcome.ENDED
    assert [s.get("price") for s in seen] == [1, 7]


def test_a_failing_sink_stops_the_watch_instead_of_reconnecting() -> None:
    """A transport failure is a reconnect; a sink failure is not. Retrying past
    it would turn a full disk into a loop that reconnects forever and stores
    nothing — which from the outside looks exactly like a healthy watch."""

    def explode(_state: dict) -> None:
        raise OSError("No space left on device")

    with pytest.raises(SinkFailed, match="No space left"):
        asyncio.run(
            watch_auction(
                AUCTION, SHARD,
                on_state=explode,
                sleep=_no_sleep,
                open_stream=_transport([PUT, GONE]),
            )
        )


def test_backoff_resets_after_a_connection_that_worked() -> None:
    """The counter only ever climbed. A watcher that dropped three times early in
    an evening then ran healthily for hours still waited 30-90 seconds before
    every later reconnect, because `attempt` was never zeroed.

    `max_attempts` should bound *consecutive* failures, not lifetime ones — a
    stream that delivered frames has proved the shard is reachable.
    """
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    asyncio.run(
        watch_auction(
            AUCTION, SHARD,
            on_state=lambda _s: None,
            sleep=record_sleep,
            # fail, fail, then a connection that delivers a frame, then fail again
            open_stream=_transport(ConnectionError, ConnectionError, [PUT], ConnectionError,
                                   [PUT, GONE]),
            jitter=lambda: 0.5,
        )
    )
    assert len(waits) >= 3
    assert waits[2] < waits[1], (
        "the wait after a working connection must drop back, not keep climbing: "
        f"{waits}"
    )


def test_a_shard_that_never_connects_still_gives_up() -> None:
    """Resetting on success must not make an unreachable auction retry for ever."""
    outcome, _ = _run(
        open_stream=_transport(*[ConnectionError] * 4),
        max_attempts=4,
    )
    assert outcome is Outcome.UNREACHABLE

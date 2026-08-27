"""One auction's subscription: connect, fold frames, reconnect, and know when to stop.

The transport is injected. That is not test decoration — it is what lets the
whole live path be exercised without a socket, which CLAUDE.md requires of the
default tier and which a streaming client is the easiest place in this repo to
break.

**Ended and dropped are different, and conflating them is how a watch dies
quietly.** The poller could not tell them apart: it counted forty consecutive
empty reads and gave up, which meant a two-minute network hiccup looked exactly
like a finished auction. Over SSE the signal is explicit — Firebase sends a
``put`` whose data is ``null`` when the node is deleted — so an ending is
observed rather than inferred, and everything else is a reconnect.

Nothing here raises on a body that is not JSON. On 2026-08-26 a
``JSONDecodeError`` from a gateway error page killed one watcher for the night
while the heartbeat kept reporting health. Malformed blocks are counted and
stepped over.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, Protocol

from fantabot.aste.models import valid_shard
from fantabot.aste.reducer import ROOT, apply_frame
from fantabot.aste.sse import Frame, FrameBuffer

#: Firebase's regional host. The shard is the only part that varies, and it is
#: not derivable — it comes off the auction's list card.
HOST = "https://fantalab-{shard}.europe-west1.firebasedatabase.app"
NODE = "/auction/{auction_id}.json"

BASE_DELAY = 1.0
MAX_DELAY = 60.0


class SinkFailed(Exception):
    """``on_state`` raised.

    Separated from every transport failure on purpose. Reconnecting past a sink
    error would turn a full disk into a quiet loop that reconnects forever and
    stores nothing — indistinguishable, from the outside, from a healthy watch
    on a slow auction.
    """


class Outcome(Enum):
    """Why a watch stopped."""

    ENDED = "ended"
    """The room closed: the node was deleted. Do not reconnect."""

    UNREACHABLE = "unreachable"
    """Reconnects ran out. The auction may still be live — this is our failure,
    not its ending, and a supervisor should say so rather than move on."""


class OpenStream(Protocol):
    """Opens a connection and yields transport chunks."""

    def __call__(self, url: str) -> AsyncIterator[str]: ...


def is_auction_gone(frame: Frame) -> bool:
    """Does this frame say the node was deleted?

    Only a **root** ``put`` with null data does. The path was not checked at
    first, so a child deletion — a nested ``put`` with null data — read as the
    room closing and dropped the auction for the rest of the evening, while the
    report called it a normal ending.
    """
    return frame.event == "put" and frame.path in ROOT and frame.data is None


def stream_url(auction_id: str, shard: str) -> str:
    """The node's URL. Refuses a shard that would leave the Firebase domain."""
    return HOST.format(shard=valid_shard(shard)) + NODE.format(auction_id=auction_id)


def _delay(attempt: int, jitter: Callable[[], float]) -> float:
    """Exponential, capped, and jittered.

    Jitter is not a nicety: nineteen shards behind one outage means every
    watcher retries in lockstep, and a synchronised stampede is how a recovering
    server is knocked over a second time.
    """
    base = min(BASE_DELAY * 2**attempt, MAX_DELAY)
    return float(base * (0.5 + jitter()))


async def watch_auction(
    auction_id: str,
    shard: str,
    *,
    open_stream: OpenStream,
    on_state: Callable[[dict[str, Any]], Awaitable[None] | None],
    sleep: Callable[[float], Awaitable[None]],
    max_attempts: int | None = None,
    jitter: Callable[[], float] = random.random,
) -> Outcome:
    """Follow one auction until it ends or reconnects run out.

    ``on_state`` receives every distinct merged state, in order. It may be sync
    or async; both are accepted so a caller writing to a file does not have to
    wrap a one-line function in a coroutine.
    """
    url = stream_url(auction_id, shard)
    state: dict[str, Any] = {}
    attempt = 0

    while max_attempts is None or attempt < max_attempts:
        buffer = FrameBuffer()
        # A connection that delivered a frame has proved the shard reachable, so
        # the next failure starts the backoff over. Without this the counter only
        # climbed: a watcher that dropped three times early in an evening waited
        # 30-90 s before every reconnect for the rest of it, however healthy the
        # stream had been in between. `max_attempts` bounds *consecutive*
        # failures, which is the thing worth bounding.
        delivered = False
        try:
            async for chunk in open_stream(url):
                for frame in buffer.feed(chunk):
                    delivered = True
                    if is_auction_gone(frame):
                        return Outcome.ENDED
                    updated = apply_frame(state, frame)
                    if updated == state:
                        continue
                    state = updated
                    try:
                        result = on_state(state)
                        if result is not None:
                            await result
                    except Exception as exc:
                        # The sink failing is not a transport problem, and must
                        # not be retried as one. The landing writer is the one
                        # component that must not fail silently; swallowing its
                        # error here would turn a full disk into a quiet
                        # reconnect loop that collects nothing.
                        raise SinkFailed(str(exc)) from exc
        except SinkFailed:
            raise
        except Exception:
            # Any transport failure is a reconnect. Deciding otherwise here is
            # how a watch ends without anyone being told.
            pass

        attempt = 1 if delivered else attempt + 1
        if max_attempts is not None and attempt >= max_attempts:
            break
        await sleep(_delay(attempt, jitter))

    return Outcome.UNREACHABLE

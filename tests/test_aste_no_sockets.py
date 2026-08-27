"""The zero-sockets rule, checked against the transport this phase actually uses.

``conftest._no_sockets`` patches ``socket.socket.connect``, ``connect_ex`` and
``socket.create_connection``. That covers synchronous clients, and the whole
suite has been synchronous until now.

This phase is not. The collector is ``httpx.AsyncClient`` over SSE, and asyncio
reaches the network through ``loop.sock_connect`` / ``loop.create_connection``,
which need not route through the patched ``socket.socket.connect`` at all. If it
does not, the rule CLAUDE.md calls load-bearing is silently unenforced for every
line of code this phase adds.

So the guard is tested against the real transport rather than assumed to hold.
The assertion is deliberately about *behaviour under the guard*, not about which
symbol asyncio happens to call — that is an implementation detail of the event
loop and would make this test a tripwire for CPython rather than for us.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

# Any address will do: the point is that the attempt is refused before it leaves
# the machine, not what is on the other end.
UNREACHABLE = "http://127.0.0.1:9/"

GUARD_SIGNATURE = "opened a socket"


def _flatten(exc: BaseException) -> list[BaseException]:
    """Every exception in the tree, groups unwrapped.

    ``httpx.AsyncClient`` runs its transport inside an anyio task group, so a
    guard that fires arrives wrapped in a ``BaseExceptionGroup`` rather than on
    its own. A first draft of this test asserted the bare type and failed — not
    because the guard had a hole, but because the assertion was narrower than
    reality. Unwrapping is the fix; loosening to ``BaseException`` would have
    made the test pass for the wrong reason.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [exc, *(inner for e in exc.exceptions for inner in _flatten(e))]
    nested = [exc]
    if exc.__cause__ is not None:
        nested += _flatten(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
        nested += _flatten(exc.__context__)
    return nested


def _assert_the_guard_fired(call: object, run: object) -> None:
    """The *guard* must refuse the attempt — not merely the network.

    An earlier draft also accepted ``httpx.TransportError``, which made the test
    worthless: the target port is closed, so a connection refusal arrives whether
    or not the guard exists, and the assertion would have passed against a
    conftest with the guard deleted. Measured on 2026-08-27: with the guard the
    tree contains ``AssertionError``; without it, ``ConnectError``. Only the
    former proves anything.
    """
    with pytest.raises(BaseException) as caught:
        run(call)
    reasons = _flatten(caught.value)
    assert any(GUARD_SIGNATURE in str(r) for r in reasons), (
        "the socket guard did not fire — the attempt reached the network stack. "
        f"got {[type(r).__name__ for r in reasons]}"
    )


def test_a_synchronous_client_is_blocked() -> None:
    """The case the existing guard was written for. Pinned so a refactor of
    conftest cannot quietly narrow it."""
    _assert_the_guard_fired(None, lambda _: httpx.get(UNREACHABLE, timeout=1))


def test_an_async_client_is_blocked() -> None:
    """The case this phase introduces, and the one the guard was never measured
    against until now."""

    async def attempt() -> None:
        async with httpx.AsyncClient(timeout=1) as client:
            await client.get(UNREACHABLE)

    _assert_the_guard_fired(attempt(), asyncio.run)


def test_an_async_stream_is_blocked() -> None:
    """SSE specifically: a streaming request opens its connection lazily, inside
    the context manager rather than at construction, which is a different code
    path through httpx than a plain request."""

    async def attempt() -> None:
        async with (
            httpx.AsyncClient(timeout=1) as client,
            client.stream("GET", UNREACHABLE) as response,
        ):
            async for _ in response.aiter_raw():
                break

    _assert_the_guard_fired(attempt(), asyncio.run)

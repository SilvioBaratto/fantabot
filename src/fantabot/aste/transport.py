"""The real SSE transport. The only place this phase opens a socket for streaming.

Kept apart from ``stream.py`` so the logic that decides when to reconnect, when
to stop and what to do with a malformed body can be exercised with a fake — which
is what keeps the default test tier socket-free while still testing the part
that gets it wrong.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

#: Long enough that a quiet auction is not mistaken for a dead connection —
#: Firebase's own keep-alive arrives well inside this — and short enough that a
#: genuinely dead socket is noticed rather than held open all evening.
READ_TIMEOUT = 90.0


async def open_stream(url: str) -> AsyncIterator[str]:
    """Yield transport chunks from an SSE endpoint.

    Chunks, not lines: the boundaries are the transport's to choose and
    ``FrameBuffer`` is built to be indifferent to them.
    """
    timeout = httpx.Timeout(connect=10.0, read=READ_TIMEOUT, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client, client.stream(
        "GET", url, headers={"Accept": "text/event-stream"}
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_text():
            yield chunk

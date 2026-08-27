"""Firebase's Server-Sent Events stream, turned into frames. Pure.

Recorded bytes corrected two assumptions this module would otherwise have been
built on, and both would have cost data:

**The keep-alive is an event, not a comment.** SSE convention says idle traffic
arrives as a line starting with ``:``. Firebase sends ``event: keep-alive`` with
``data: null``. A parser written to the convention treats those as unknown
events, and — worse — a reducer that treats ``data: null`` as a payload would
wipe the board on every idle tick.

**Frames arrive on no particular boundary.** The transport happened to deliver
whole frames during the 100-second recording, which is exactly why that cannot
be relied on. ``FrameBuffer`` holds an incomplete tail rather than emitting it.

Nothing here raises on a malformed payload. A gateway error page is not JSON,
and raising would end one auction's watch for the night — the silent death this
phase exists to stop repeating.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class Frame:
    """One parsed event.

    ``path`` and ``data`` are lifted out of the payload because every auction
    frame carries them, and callers that had to reach through ``payload["data"]``
    would all reach the same way.
    """

    event: str
    path: str | None
    data: Any


def _frame(block: str) -> Frame | None:
    event: str | None = None
    payload_lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload_lines.append(line[len("data:") :].strip())
        # Anything else — including a genuine SSE comment — is not addressed to
        # us. Ignored rather than refused: an unknown line must not cost the
        # frame it sits beside.
    if event is None:
        return None

    raw = "\n".join(payload_lines)
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict) and "data" in payload:
        return Frame(event=event, path=payload.get("path"), data=payload["data"])
    return Frame(event=event, path=None, data=payload)


def parse(text: str) -> list[Frame]:
    """Every complete frame in ``text``. An incomplete tail is discarded."""
    blocks = text.split(SEPARATOR)
    return [frame for block in blocks if (frame := _frame(block)) is not None]


class FrameBuffer:
    """Accumulates transport chunks and emits frames as they complete.

    Stateful, but only over its own buffer: no I/O, no clock, no network. That
    keeps the whole live path testable by replaying a recording one byte at a
    time, which is the strongest statement of chunk-independence available.
    """

    def __init__(self) -> None:
        self._pending = ""
        self.malformed = 0
        """Blocks that could not be parsed into a frame.

        Dropping them is right — a gateway error page must not end an auction's
        watch — but dropping them *silently* is the failure this phase exists to
        stop. A supervisor that never sees this counter move cannot tell a quiet
        auction from a stream that has been serving HTML for ten minutes.
        """

    def feed(self, chunk: str) -> list[Frame]:
        """Frames completed by this chunk. The rest is held for the next one."""
        self._pending += chunk
        if SEPARATOR not in self._pending:
            return []
        *complete, self._pending = self._pending.split(SEPARATOR)
        frames = []
        for block in complete:
            frame = _frame(block)
            if frame is not None:
                frames.append(frame)
            elif block.strip():
                self.malformed += 1
        return frames

    def flush(self) -> list[Frame]:
        """Whatever is left when the stream closes cleanly."""
        remainder, self._pending = self._pending, ""
        frame = _frame(remainder)
        return [frame] if frame is not None else []

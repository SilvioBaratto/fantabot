"""Append-only JSONL of every decision the live room made. The evening's only record.

One line per cycle: what was on the block, what we thought it was worth and why, what we did,
and what we had left. After the asta it is the only way to ask whether the floor was set right
— the room keeps no history we can read, and a heartbeat scrolls away.

**It must never be able to wait on a database.** This module joins `CAPTURE` in
`tests/application/test_aste_outage.py`, which proves structurally — not by inspection — that
nothing here can reach `adapters.persistence`. The rule is the harvest collector's and it
applies for the same reason: an outage must cost catch-up time and never a record. A journal
that blocks on Postgres at 21:47 loses the lot it was recording *and* the one after it.

Opened once and kept open, flushed per line. Reopening per cycle costs a syscall every two
seconds for the whole evening; never flushing loses the tail on the crash the journal exists
to explain.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any


class RoomJournal:
    """A sink for `RoomTracker`'s frames. Used as a context manager, or closed by hand."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    def write(self, row: Mapping[str, Any]) -> None:
        """One decision. Never raises: a journal that can end the evening is worse than none.

        A record we cannot write is worth less than the lot we would lose writing it, so a
        failure here is swallowed rather than propagated into the bid loop.
        """
        try:
            self._handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._handle.flush()
        except (OSError, TypeError, ValueError):
            return

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> RoomJournal:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

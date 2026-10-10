"""The landing zone: append-only JSONL, written before anything interprets it.

**This is the component that must not fail.** Every other step in the phase can
be rerun — the reducer, the reconstruction, the load into Postgres all read from
here. A frame that never reached disk is gone, and an evening of auctions does
not come back.

Three decisions follow from that, and each costs something:

**Append and close per record**, not a held handle. It costs a syscall per
write. It buys the property the 2026-08-26 recording depended on: the collector
was killed eleven times in eight hours, and a buffered handle would have lost
whatever the buffer held on each of them. What was actually lost was between 13
and 28 seconds of reconnect time, never a written record.

**Disk before database.** The loader carries records into Postgres separately,
so a database outage cannot stop collection. Decided 2026-08-27 for exactly the
reason above: the file survived eleven kills; a socket would not have.

**A truncated final line is survivable, not fatal.** A kill during a write leaves
a partial record. Losing that one is the accepted cost; refusing to read the file
because of it is not. That rule is enforced by the *reader*. Both readers this module's docstring used to
name went on 2026-09-24, each reached by nothing but its own tests:
``application/harvest_loader.iter_records`` and this module's own ``read_records``,
which were the two halves of exactly the split the line below warns about. The live
path reads a window through ``application/harvest_loader``'s incremental fold.

The record shape is identical to what the poller wrote, so the loader has one
reader rather than two.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LandingZone:
    """An append-only JSONL sink for observed auction states."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.written = 0
        self._tail_checked = False

    def write(self, auction_id: str, state: Mapping[str, Any]) -> None:
        """Append one observation. Durable when it returns."""
        record = {
            "seen_at": datetime.now(UTC).isoformat(),
            "auction_id": auction_id,
            "state": dict(state),
        }
        # ensure_ascii=False: player names carry accents (Konaté, Lucumì), and
        # escaping them still parses but stops matching the recorded evening
        # byte for byte — which is the comparison T16 rests on.
        line = json.dumps(record, ensure_ascii=False)
        payload = (line + "\n").encode("utf-8")
        with self.path.open("ab+") as handle:
            if not self._tail_checked:
                # A tail without a newline (ENOSPC, power loss, an unmounted SSD) would
                # fuse the first record of this run into the fragment, and the reader
                # drops the fused line: one lost observation becomes two.
                if handle.seek(0, 2) > 0:
                    handle.seek(-1, 2)
                    if handle.read(1) != b"\n":
                        payload = b"\n" + payload
                self._tail_checked = True
            handle.write(payload)
        self.written += 1

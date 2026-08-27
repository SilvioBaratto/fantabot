"""Carrying the landing zone into Postgres, incrementally.

The database is deliberately not on the collection critical path: the collector
appends to a file and this reads from it. An outage therefore costs catch-up
time and never a record — which is the whole reason the split exists.

**The rule that makes it safe: never consume a line the writer has not
finished.** The two processes share a file with no lock between them, so a read
can land mid-append. Taking a partial line and advancing past it would lose that
record permanently, because the offset would never come back for it. Everything
after the last newline is left where it is.

Reading is separated from writing so the correctness — offsets, partial lines,
a checkpoint that outlived its file — is testable with no database at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Where a checkpoint lives: beside its landing zone, so moving one moves both.
SUFFIX = ".offset"


class Checkpoint:
    """How far into a landing zone the loader has already read.

    A plain file rather than a table, on purpose. The offset describes a file on
    this disk, and putting it in Postgres would make resuming depend on the very
    thing the landing zone exists to decouple from.
    """

    def __init__(self, landing: Path) -> None:
        self.path = landing.with_name(landing.name + SUFFIX)

    def read(self) -> int:
        try:
            return int(self.path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            # No checkpoint, or one written by an interrupted process. Starting
            # from zero is slow and correct; guessing is fast and not.
            return 0

    def write(self, offset: int) -> None:
        self.path.write_text(str(offset), encoding="utf-8")


def read_from(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Complete records after ``offset``, and where to resume.

    Returns the offset unchanged when there is nothing new, so a caller can
    treat "no progress" as a plain equality rather than a sentinel.
    """
    try:
        size = path.stat().st_size
    except OSError:
        # Collection may not have started yet. That is a normal state, not a
        # failure, and `--follow` has to survive it.
        return [], offset if offset == 0 else 0

    if offset > size:
        # The landing zone was rotated or replaced and is now shorter than the
        # checkpoint remembers. Seeking past the end would read nothing for
        # ever, which looks exactly like a quiet evening.
        offset = 0

    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()

    tail = chunk.rfind(b"\n")
    if tail == -1:
        # Not even one complete line yet.
        return [], offset

    complete = chunk[: tail + 1]
    records: list[dict[str, Any]] = []
    for line in complete.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A line that is whole but not JSON. Skipped for the same reason the
            # landing reader skips one: losing a record beats refusing the file.
            continue
    return records, offset + len(complete)

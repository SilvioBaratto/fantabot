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

**The reader lives here too, and that is the point of this module.** The row schema was
stated in five places and had already drifted: `bargain_spent`, `bargain_allowance` and
`error` were written and read by nobody, the bargain pair having landed in `44cfe89` — an
ancestor of the viewer commit `86acb6c`, so the reader was written *after* those keys
existed and dropped them anyway. `waiting` and `error` rows carry two or three keys, so a
reader that knows only the decision fields renders both as a row of nulls, and telling a
skipped poll from a crash is `error_row`'s entire purpose. The field list, the
skip-and-count rule, the 1-based index and newest-first are therefore stated once, below.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
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


# -- reading it back ---------------------------------------------------------------------

#: The file's vocabulary — every key a writer emits, as it appears on the line. Not the
#: row's attribute names: `owned` is 27 ids in the file and an `owned_count` on the row,
#: because 5,192 rosters is an inventory and the row is a decision.
#:
#: `tests/adapters/files/test_room_journal_read.py` reads `application/asta_room.py`'s own
#: dict literals and fails when one of them is missing from this set. Nothing structural
#: connects the two ends — the journal is injected as a callable so the outage rule holds,
#: which is exactly how three keys came to be written and never read.
ROW_FIELDS: frozenset[str] = frozenset(
    {
        "at_ms",
        "node",
        "lot",
        "name",
        "price",
        "walk_away",
        "provenance",
        "decision",
        "reason",
        "credits_left",
        "max_cap",
        "owned",
        "bargain_spent",
        "bargain_allowance",
        "error",
        "cycle_ms",
    }
)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One cycle, parsed. Every field but `index` is optional, and has to be.

    The file spans two commands and several weeks of them: `cycle_ms` was added after the
    2026-09-01 evening was recorded, `asta room` and `asta bid` both append here, and
    neither marks a run boundary. `RoomJournal.write` serialises with `default=str`, so a
    field's *type* is not guaranteed by the file either — a value that will not coerce is
    dropped to `None` rather than costing the evening its record.
    """

    #: 1-based line number in the file, oldest = 1. Reversing and paging must not cost a
    #: row its citation: the audit that found the three bidder defects cites line numbers.
    index: int
    at_ms: int | None = None
    node: str | None = None
    lot: str | None = None
    name: str | None = None
    price: float | None = None
    #: Null on 4,501 of the 5,192 recorded rows — that is what defect B2 looks like in the
    #: file, so it stays a null and never becomes a zero.
    walk_away: float | None = None
    provenance: str | None = None
    decision: str | None = None
    reason: str | None = None
    credits_left: float | None = None
    max_cap: float | None = None
    #: The count of the rosa, not its ids.
    owned_count: int | None = None
    #: Credits already gone on lots the plan never named, and the evening's ceiling for
    #: them. Written since `44cfe89` and read by nothing until this dataclass.
    bargain_spent: int | None = None
    bargain_allowance: int | None = None
    #: The exception type of a poll that raised. What makes an `error` row distinguishable
    #: from a `waiting` one — both carry two or three keys and nothing else.
    error: str | None = None
    cycle_ms: float | None = None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _entry(index: int, raw: Mapping[str, Any]) -> JournalEntry:
    owned = raw.get("owned")
    return JournalEntry(
        index=index,
        at_ms=_as_int(raw.get("at_ms")),
        node=_as_text(raw.get("node")),
        lot=_as_text(raw.get("lot")),
        name=_as_text(raw.get("name")),
        price=_as_float(raw.get("price")),
        walk_away=_as_float(raw.get("walk_away")),
        provenance=_as_text(raw.get("provenance")),
        decision=_as_text(raw.get("decision")),
        reason=_as_text(raw.get("reason")),
        credits_left=_as_float(raw.get("credits_left")),
        max_cap=_as_float(raw.get("max_cap")),
        owned_count=len(owned) if isinstance(owned, list) else None,
        bargain_spent=_as_int(raw.get("bargain_spent")),
        bargain_allowance=_as_int(raw.get("bargain_allowance")),
        error=_as_text(raw.get("error")),
        cycle_ms=_as_float(raw.get("cycle_ms")),
    )


def read_rows(path: Path) -> tuple[tuple[JournalEntry, ...], int]:
    """Every row in `path`, **newest first**, and the count of lines that did not parse.

    Newest-first here rather than at each caller, because it is the same decision every
    time and the reason is the file's: an evening is read from its end.

    **A line that does not parse is skipped and counted, not fatal** — the landing zone's
    rule, and sharper here: the journal flushes per line, so the only line a crash can
    tear is the last one, which tail-first is the *first* row a viewer renders. A reader
    that raised on it would show nothing at all for the evening it exists to explain. A
    missing file is empty rather than an error — "no journal yet" and "you are looking in
    the wrong place" are the same screen, and naming the path is the caller's job. A file
    that *exists* and cannot be read is a different answer and raises, because a viewer
    that renders it as "no journal yet" sends the operator looking for a path that is
    already right.

    The whole file is parsed, because a JSONL has no index and the total is part of the
    answer. Measured against the 2026-09-01 evening — 1.6 MB, 5,192 rows, 0 skipped — at
    **29.8 ms**, well inside the 2 s budget for a synchronous panel read.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return (), 0

    rows: list[JournalEntry] = []
    skipped = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(decoded, dict):
            rows.append(_entry(number, decoded))
        else:
            skipped += 1

    rows.reverse()
    return tuple(rows), skipped

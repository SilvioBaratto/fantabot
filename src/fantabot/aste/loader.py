"""Carrying the landing zone into Postgres, incrementally.

The database is deliberately not on the collection critical path: the collector
appends to a file and this reads from it. An outage therefore costs catch-up
time and never a record — which is the whole reason the split exists.

**Events are incremental; assignments are not, and cannot be.** A checkpointed
window is right for `asta_event`, which is append-only. It is wrong for
`asta_assignment`: ``reconstruct`` holds its ladders in locals, so a window
starting mid-turn rebuilds from nothing, and the upsert is DO UPDATE — the short
ladder then overwrites the complete one and the checkpoint never returns for the
rungs it skipped. The sale survives, the price survives, only the ladder is
quietly truncated, which is the one thing this collector exists to keep.

So assignments are rebuilt from the whole landing zone each pass. It costs a
re-read — measured at roughly one second for 144,518 records against a ten-second
interval — and it is the only shape that cannot lose a rung.

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
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fantabot.aste.models import Assignment

#: Where a checkpoint lives: beside its landing zone, so moving one moves both.
SUFFIX = ".offset"


class LandingZoneMissing(FileNotFoundError):
    """The landing zone does not exist, so nothing is collecting into it.

    Its own type because the remedy is a specific command, and because the
    alternative — an empty window — is indistinguishable from the two states
    that are genuinely fine.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"no landing zone at {path} — nothing is collecting into it.\n"
            f"Start one with: fantabot aste-collect --seed <seed> --out {path}"
        )
        self.path = path


class CachedPlayerIds:
    """The reference set of `fantacalcio_id`s, fetched at most once per TTL.

    `known_player_ids()` was called inside the pass body, so following a
    landing zone at the default ten-second interval opened a session and pulled
    1,492 ids six times a minute — for a table that only changes when someone
    runs `db-import`.

    Not cached for the life of the process, though. A follow left running for a
    day would keep nulling `fantacalcio_id` for a player imported that morning,
    and the null is what makes an assignment show up as unlinked. A window is
    the honest answer: rare enough to stop being a per-pass query, short enough
    that an import is picked up without a restart.

    A failed fetch is not stored. `aste-load --follow` already survives a
    database outage by skipping the pass; a cache that remembered the failure
    would go on unlinking players long after the database came back.
    """

    def __init__(self, fetch: Callable[[], frozenset[int]], ttl: float = 300.0) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._value: frozenset[int] | None = None
        self._fetched_at = 0.0

    def get(self, *, now: float) -> frozenset[int]:
        if self._value is None or now - self._fetched_at >= self._ttl:
            value = self._fetch()
            self._value, self._fetched_at = value, now
        return self._value


class SeedRows:
    """The seed, re-read each pass, with the last good one as the fallback.

    `aste-load --follow` read it once at startup. The collector re-reads its own
    and adopts auctions that opened since, so within an hour the loader was
    seeing events for auctions it had never heard of — and dropping them, then
    advancing its checkpoint past them. Not a delay: a loss. Measured 2026-08-27
    at 22:07, `595 record(s) dropped — unknown auction 595`.

    Re-reading is cheap enough to do every pass — a 90 kB JSON parse against a
    ten-second interval — which is why it is here rather than behind a TTL like
    `CachedPlayerIds`. The database query needed the window; this does not.

    `aste-scan` rewrites this file while the loader reads it, so a half-written
    or briefly missing seed keeps the previous rows and costs one pass. Turning
    every auction into an unknown one for a cycle is the exact loss the re-read
    exists to stop.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rows: list[Any] = []
        self.failures = 0

    def read(self) -> list[Any]:
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.failures += 1
            return self._rows
        if not isinstance(rows, list):
            # A seed is a list of rows. Anything else is a file that is not this
            # file, and feeding it forward would turn every auction unknown just
            # as surely as a parse error does.
            self.failures += 1
            return self._rows
        self._rows = rows
        return rows


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
    except OSError as exc:
        # A missing landing zone is not a quiet pass. Returning an empty window
        # made "the collector was never started" print exactly like "nothing
        # happened yet" and "everything is already loaded" — and an operator sat
        # watching that on 2026-08-27, believing the auctions were idle.
        raise LandingZoneMissing(path) from exc

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


def assignments_for_pass(
    landing: Path, new_records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Assignment rows for a pass that carried ``new_records``.

    Rebuilt from the **whole** landing zone rather than the window, for the
    reason in the module docstring. Returns nothing when the pass carried
    nothing, so a quiet pass does not rewrite rows it has no news about.

    Kept here rather than in the CLI so the windowing rule has one home and one
    test, instead of being an implicit property of the command.
    """
    if not new_records:
        return []
    from fantabot.aste.reconstruct import reconstruct

    return _rows(reconstruct(read_records(landing)))


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every complete record in ``path``, from the beginning."""
    records, _offset = read_from(path, 0)
    return records


def _rows(assignments: Iterable[Assignment]) -> list[dict[str, Any]]:
    """Assignment value types as the rows the repository writes.

    The listone bridge is applied by the caller, which holds it; this only
    reshapes, so the two halves stay independently testable.
    """
    return [
        {
            "asta_id": a.auction_id,
            "player_uuid": a.player_id,
            "fantacalcio_id": None,
            "price": a.price,
            "buyer_team_id": a.buyer_team_id,
            "closed_at_ms": a.closed_at_ms,
            "ladder": [
                {"price": b.price, "team_id": b.team_id, "at_ms": b.at_ms} for b in a.ladder
            ],
        }
        for a in assignments
    ]

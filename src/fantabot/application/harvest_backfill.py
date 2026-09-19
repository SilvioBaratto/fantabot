"""Loading a recorded collector log — what may be asked for, and the run itself (T22).

`domain/harvest/backfill.py` builds the rows and says why that is pure. This module is the
layer above it: the three refusals, the enumeration of what the harvest home actually
holds, and the read-build-write sequence. All of it lived inside `aste_backfill`'s Typer
body, where the app could not reach any of it.

**The enumeration is the half that did not exist anywhere.** Nothing had ever decided what
a *candidate* is, because the CLI took a path from a human who knew which file they meant.
A picker cannot ask, so the rule has to be written down: a candidate is a file whose first
line is a collector state. The home is full of near-misses that a glob would offer —
`live.jsonl.offset` and `.state` (a position in a file, not a file), a zero-byte
`live.jsonl` that a collect which never connected leaves, and
`assignments_2026-08-26.jsonl`, which has the extension and the `auction_id` key and no
`state` at all. That last one is the reason the test is a sniff and not a suffix rule:
`read_jsonl` accepts it, `build` drops all 18 records as malformed, and the run reports
success having loaded nothing.

**`live.jsonl` is offered, and flagged.** It is the active landing zone and `harvest load
--follow` owns its offset — but a backfill only reads it, and all three writes are
upserts, so there is nothing for it to disturb. Hiding it would make the one file with
1.3 GB in it the one file the app cannot reach. The flag is so the operator knows which
of the four names is the live one; it is not a refusal.

**The seed is a parameter, not a default, because the home holds three.** Backfilling
`events_2026-08-26.jsonl` against today's `seed.json` is not an error and reports as a
success: every auction the current seed no longer describes is counted as
`unknown_auction` and dropped. `BackfillReport.dropped` is where that shows up, which is
why a dry run comes before a write rather than after it.

**A missing listone is a warning and never a refusal.** It costs every assignment its
player link and no rows at all, and an auction price is unrepeatable — a stale bridge is
not a reason to lose an evening.

Nothing here commits, for `exclusions.py`'s reason: the transaction boundary belongs to
the caller that knows what else is in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fantabot.domain.harvest.backfill import BuiltRows, build, read_jsonl

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The name the harvest home gives its live landing zone. A constant rather than a literal
#: because two things read it — the flag on the candidate, and `harvest load` itself.
LIVE_LANDING = "live.jsonl"


class InvalidBackfill(ValueError):
    """The backfill as asked for cannot be run, and nothing was read.

    Named so each surface refuses its own way — a `typer.Exit(2)` with the line printed,
    a 400 with the line in the body — without either having to tell this apart from a
    database being down. Raised before any file is opened, which is what makes the
    message the whole answer.
    """


@dataclass(frozen=True)
class BackfillInputs:
    """Three paths and a format, all four checked. What a run was actually given."""

    events: Path
    seed: Path
    listone: Path
    asta_type: str
    #: Whether the bridge is there. Not a refusal — see the module docstring — but the
    #: caller has to say so, because the run's assignments all carry a null player link
    #: and the report alone looks like a database that is behind the listone.
    listone_present: bool


@dataclass(frozen=True)
class LogCandidate:
    """One collector log the picker may offer, with what an operator needs to choose."""

    name: str
    bytes: int
    #: ISO-8601, UTC. `None` only if the file vanished between the scan and the stat.
    mtime: str | None
    #: The active landing zone. Offered, flagged, never refused.
    live: bool


@dataclass(frozen=True)
class SeedCandidate:
    """One scan seed the picker may offer. `rows` is how many auctions it describes."""

    name: str
    rows: int
    mtime: str | None


@dataclass(frozen=True)
class Candidates:
    """Everything in one harvest home a backfill could be pointed at.

    `exists` is separate from two empty tuples: `harvest_dir()` names a directory nobody
    has created yet on a fresh install, and "the home is not there" names a command while
    "the home is empty" does not.
    """

    home: Path
    exists: bool
    logs: tuple[LogCandidate, ...] = ()
    seeds: tuple[SeedCandidate, ...] = ()
    #: Why a home that *is* there yielded nothing — a permissions refusal, say. `None`
    #: when the listing succeeded, and `None` when there is no home at all: that case is
    #: `exists=False`, and it names a command rather than a dialog.
    error: str | None = None


@dataclass(frozen=True)
class BackfillReport:
    """What one run built, and — when it wrote — what the table holds afterwards."""

    states: int
    auctions: int
    events: int
    assignments: int
    unlinked_players: int
    #: Per-reason counts. `unknown_auction` is the one that tells a mismatched seed from
    #: a clean run, so it is carried whole rather than summed.
    dropped: Any
    written: bool
    #: `count_assignments()` after the write, or `None` for a dry run. The corpus total,
    #: which is the number that says the evening landed.
    total_assignments: int | None = None


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        return None


def clean_backfill(
    *, events: Path, seed: Path, listone: Path, asta_type: str
) -> BackfillInputs:
    """The validated inputs, or `InvalidBackfill`. Reads no file's contents.

    The format is checked first and deliberately: `asta_type` is NOT NULL with two legal
    values, and a typo caught here beats a constraint violation after building 144,518
    rows. The paths are then checked by name, because a message naming the file is the
    whole remedy — the operator either meant another one or has not adopted the home yet.
    """
    from fantabot.adapters.persistence.models.aste import ASTA_TYPES

    if asta_type not in ASTA_TYPES:
        raise InvalidBackfill(
            f"{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}"
        )
    for label, path in (("events", events), ("seed", seed)):
        if not path.exists():
            raise InvalidBackfill(f"{label} file not found: {path}")
    return BackfillInputs(
        events=events,
        seed=seed,
        listone=listone,
        asta_type=asta_type,
        listone_present=listone.exists(),
    )


def _is_collector_log(path: Path) -> bool:
    """Whether `path`'s first line is a collector state.

    One line, not the file: the candidates include a 1.3 GB landing zone and a picker
    that reads all of them to draw itself is a picker nobody waits for.

    **Both keys are required, and each catches a different near-miss.** `seen_at` is what
    `event_rows` times a record by, and the file without it is
    `assignments_2026-08-26.jsonl` — same extension, same `auction_id`, a reconstruction
    output rather than a recording. `state` is the record itself, and the file without
    *it* is anything counted under `DroppedEvents.malformed_state`: a log of those loads,
    builds nothing and reports success. `auction_id` is deliberately not among them,
    because both near-misses have it.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                return isinstance(record, dict) and "seen_at" in record and "state" in record
    except (OSError, ValueError):
        return False
    # Ran out of lines: an empty or all-blank file. Not a candidate — see the module
    # docstring on what a collect that never connected leaves behind.
    return False


def _seed_rows(path: Path) -> int | None:
    """How many auctions a seed describes, or `None` if it is not a seed at all."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(rows) if isinstance(rows, list) else None


def candidates(home: Path) -> Candidates:
    """What the picker offers, read from one directory. Never raises.

    Sorted by name rather than by mtime, so the same home draws the same list twice and
    an operator's muscle memory survives a collect finishing mid-session.
    """
    if not home.is_dir():
        return Candidates(home=home, exists=False)

    logs: list[LogCandidate] = []
    seeds: list[SeedCandidate] = []
    try:
        entries = sorted(home.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return Candidates(home=home, exists=True, error=type(exc).__name__)

    for path in entries:
        if not path.is_file():
            continue
        # The suffix is what excludes every sidecar, and it is the only thing that needs
        # to. `harvest load` writes `live.jsonl.offset`, `.state` and two `.lock` files
        # beside a landing zone; each ends in its own suffix, so none of the four is ever
        # a `.jsonl` or a `.json`. A tuple of sidecar names alongside this read as a
        # second guard and was a test that could not fail.
        if path.suffix == ".jsonl":
            if _is_collector_log(path):
                logs.append(
                    LogCandidate(
                        name=path.name,
                        bytes=path.stat().st_size,
                        mtime=_mtime(path),
                        live=path.name == LIVE_LANDING,
                    )
                )
        elif path.suffix == ".json":
            rows = _seed_rows(path)
            if rows is not None:
                seeds.append(SeedCandidate(name=path.name, rows=rows, mtime=_mtime(path)))

    return Candidates(home=home, exists=True, logs=tuple(logs), seeds=tuple(seeds))


def build_backfill(
    inputs: BackfillInputs, *, known_players: frozenset[int] | None = None
) -> tuple[BuiltRows, int]:
    """Read the three files and build every row. Returns the rows and the state count.

    No database, so this is the whole expensive half and it is checkable in the default
    tier — which is what `--dry-run` has always been for.
    """
    from fantabot.adapters.http.fantalab import listone as listone_module

    states = read_jsonl(inputs.events)
    seed_rows = json.loads(inputs.seed.read_text(encoding="utf-8"))
    raw_bridge = (
        json.loads(inputs.listone.read_text(encoding="utf-8")) if inputs.listone_present else {}
    )
    bridge = listone_module.entries_only(raw_bridge) if isinstance(raw_bridge, dict) else {}
    built = build(states, seed_rows, bridge, inputs.asta_type, known_players)
    return built, len(states)


def report_of(built: BuiltRows, states: int, *, written: bool, total: int | None) -> BackfillReport:
    """A `BackfillReport` over rows already built. Pure, and the one place the shape is
    assembled — a dry run and a write differ in two fields and in nothing else."""
    return BackfillReport(
        states=states,
        auctions=len(built.auctions),
        events=len(built.events),
        assignments=len(built.assignments),
        unlinked_players=built.unlinked_players,
        dropped=built.dropped_events,
        written=written,
        total_assignments=total,
    )


def run_backfill(
    inputs: BackfillInputs, *, session: Session | None = None
) -> BackfillReport:
    """Build, and write when given a session. Does not commit — see the module docstring.

    `session is None` is the dry run, and it is the *absence* of a connection rather than
    a flag beside one: a dry run that held an open session would be a dry run the socket
    guard could not prove opened nothing.
    """
    if session is None:
        built, states = build_backfill(inputs)
        return report_of(built, states, written=False, total=None)

    from fantabot.adapters.persistence.repositories.aste import AsteRepository

    repo = AsteRepository(session)
    # Read before the build, because `build` needs it: an unknown `fantacalcio_id` is
    # dropped to NULL rather than violating the foreign key, and an auction price is
    # unrepeatable while a stale `players` is not a reason to lose one.
    built, states = build_backfill(inputs, known_players=repo.known_player_ids())
    repo.upsert_auctions(built.auctions)
    repo.upsert_events(built.events)
    repo.upsert_assignments(built.assignments)
    return report_of(built, states, written=True, total=repo.count_assignments())

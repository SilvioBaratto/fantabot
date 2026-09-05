"""The corpus panel — what the harvest actually put in the database, per format.

Built before anything with a lifecycle, and `todo/TODO.md` §2 says why: it is the
instrument every later increment is graded on, and without it "collection worked" is
unfalsifiable. §1.1 is the case in point — the Classic corpus read as 2.1 million events
and zero sales for over a week, and no screen could tell "nothing was collected" from
"everything was collected and nothing joined".

Read-only, and it degrades open like `/db/health` for the same reason: this is a status
read, and a status read that 500s when the database is down reports nothing about the
one thing it exists to report.

**The filter travels with the answer.** `planner_sales` is `clearing_sales` under one
league shape, and read against another it is a different number — so the shape is in the
response rather than assumed by whoever renders it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class CorpusFormat(BaseModel):
    asta_type: str
    rooms: int
    rooms_with_events: int
    events: int
    assignments: int
    assignments_with_buyer: int
    assignments_with_player: int
    planner_sales: int


class Corpus(BaseModel):
    ok: bool
    #: The shape `planner_sales` was counted under. Rendered beside the number.
    num_credits: int = 500
    num_teams: int = 8
    formats: list[CorpusFormat] = []
    error: str | None = None


class _AsteRepo(Protocol):
    def corpus_summary(
        self, *, num_credits: int = ..., num_teams: int = ...
    ) -> list[Any]: ...


def read_corpus(
    repo: _AsteRepo, *, num_credits: int = 500, num_teams: int = 8
) -> Corpus:
    """Assemble a Corpus from an AsteRepository-like object (pure; unit-testable)."""
    try:
        rows = repo.corpus_summary(num_credits=num_credits, num_teams=num_teams)
    except Exception as exc:  # noqa: BLE001 — report a down DB, don't crash
        return Corpus(
            ok=False, num_credits=num_credits, num_teams=num_teams, error=type(exc).__name__
        )
    return Corpus(
        ok=True,
        num_credits=num_credits,
        num_teams=num_teams,
        formats=[
            CorpusFormat(
                asta_type=row.asta_type,
                rooms=row.rooms,
                rooms_with_events=row.rooms_with_events,
                events=row.events,
                assignments=row.assignments,
                assignments_with_buyer=row.assignments_with_buyer,
                assignments_with_player=row.assignments_with_player,
                planner_sales=row.planner_sales,
            )
            for row in rows
        ],
    )


@router.get("/harvest/corpus", response_model=Corpus, tags=["harvest"])
def harvest_corpus(num_credits: int = 500, num_teams: int = 8) -> Corpus:
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.aste import AsteRepository

    try:
        with database_manager.get_session() as session:
            return read_corpus(
                AsteRepository(session), num_credits=num_credits, num_teams=num_teams
            )
    except Exception as exc:  # noqa: BLE001 — a session that cannot open is still a report
        return Corpus(
            ok=False, num_credits=num_credits, num_teams=num_teams, error=type(exc).__name__
        )


class SeedPanel(BaseModel):
    """The registry beside the corpus: what a scan added, before anything was collected.

    Registered is not collected — the seed grows on every scan whether or not a single
    frame arrived — so this panel is deliberately *next to* the corpus rather than part
    of it, and the counts are never summed together.
    """

    ok: bool
    path: str
    exists: bool
    mtime: str | None = None
    rows: int = 0
    #: Per-format split, counted after the merge the scan wrote.
    formats: dict[str, int] = {}
    error: str | None = None


def read_seed(path: Path) -> SeedPanel:
    """Count the seed file at `path`, per format (pure but for the one read).

    A row with no format is a poller-era row and is Mantra, the same fallback
    `registry.from_seed_row` applies and for the same reason: the eleven-field file
    predates storing the format, and reading those rows as anything else is how 185
    Classic auctions came to be labelled Mantra.
    """
    from fantabot.domain.harvest.registry import SEED_FIELDS

    fmt_index = SEED_FIELDS.index("asta_type")
    if not path.exists():
        # Not the same answer as zero rows: only one of the two names a command.
        return SeedPanel(ok=True, path=str(path), exists=False)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        formats: dict[str, int] = {}
        for row in rows:
            asta_type = row[fmt_index] if len(row) > fmt_index and row[fmt_index] else "mantra"
            formats[str(asta_type)] = formats.get(str(asta_type), 0) + 1
    except Exception as exc:  # noqa: BLE001 — a torn seed is a report, not a 500
        return SeedPanel(ok=False, path=str(path), exists=True, error=type(exc).__name__)
    return SeedPanel(
        ok=True,
        path=str(path),
        exists=True,
        mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
        rows=len(rows),
        formats=dict(sorted(formats.items())),
    )


@router.get("/harvest/seed", response_model=SeedPanel, tags=["harvest"])
def harvest_seed() -> SeedPanel:
    from fantabot.config import harvest_dir

    return read_seed(harvest_dir() / "seed.json")

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

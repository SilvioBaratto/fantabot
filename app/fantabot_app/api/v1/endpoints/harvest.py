"""The corpus panel — what the harvest actually put in the database, per format.

Built before anything with a lifecycle, and T15 says why (its spec is archived at
`tasks/archive/fantalab-in-the-app-spec.md`): it is the
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

The reads above are the module's original job. The harvest *lifecycle* triggers live here
too rather than in `actions.py`, because they are not that module's idiom: an action is a
use case on a daemon thread, and these spawn and supervise a child process
(`infrastructure/processes.py`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry

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
    #: What a collect would use for `--pool` if it were not told otherwise. Carried here
    #: so the UI can pre-fill against `rows` without hardcoding a constant that has
    #: already moved once — it was 250 on the evening the population was 649.
    default_pool: int = 0
    error: str | None = None


def read_seed(path: Path) -> SeedPanel:
    """Count the seed file at `path`, per format (pure but for the one read).

    A row with no format is a poller-era row and is Mantra, the same fallback
    `registry.from_seed_row` applies and for the same reason: the eleven-field file
    predates storing the format, and reading those rows as anything else is how 185
    Classic auctions came to be labelled Mantra.
    """
    from fantabot.application.harvest_supervisor import DEFAULT_POOL
    from fantabot.domain.harvest.registry import SEED_FIELDS

    fmt_index = SEED_FIELDS.index("asta_type")
    if not path.exists():
        # Not the same answer as zero rows: only one of the two names a command.
        return SeedPanel(ok=True, path=str(path), exists=False, default_pool=DEFAULT_POOL)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        formats: dict[str, int] = {}
        for row in rows:
            asta_type = row[fmt_index] if len(row) > fmt_index and row[fmt_index] else "mantra"
            formats[str(asta_type)] = formats.get(str(asta_type), 0) + 1
    except Exception as exc:  # noqa: BLE001 — a torn seed is a report, not a 500
        return SeedPanel(
            ok=False,
            path=str(path),
            exists=True,
            default_pool=DEFAULT_POOL,
            error=type(exc).__name__,
        )
    return SeedPanel(
        ok=True,
        path=str(path),
        exists=True,
        mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(),
        rows=len(rows),
        formats=dict(sorted(formats.items())),
        default_pool=DEFAULT_POOL,
    )


@router.get("/harvest/seed", response_model=SeedPanel, tags=["harvest"])
def harvest_seed() -> SeedPanel:
    from fantabot.config import harvest_dir

    return read_seed(harvest_dir() / "seed.json")


class JobStarted(BaseModel):
    job_id: str


@router.post("/harvest/load", response_model=JobStarted, tags=["harvest"])
def harvest_load(asta_type: str = "mantra", follow: bool = True) -> JobStarted:
    """Carry the landing zone into Postgres, supervised as a child process.

    A subprocess and not a thread — `processes.py` carries the argument. Proven here
    before it is pointed at `collect`: the loader is idempotent and restartable, the
    collector is the thing that cannot be re-run, and debugging process control against
    the irreplaceable one is the wrong order.

    **`asta_type` is not the filter the scan is forbidden.** A load reads one format's
    auctions out of a seed that holds both, so the format is a parameter of this read.
    What the app must never own is a *collection-time* filter — the thing that decides
    which auctions are ever heard from at all.

    The app never resets a checkpoint (§3.2 of the archived phase spec,
    `tasks/archive/fantalab-in-the-app-spec.md`), and there is nothing here that
    could: the offset is the loader's, and the only command that moves it backwards stays
    a terminal act.
    """
    from fantabot.adapters.files.lock import LOADER
    from fantabot.adapters.persistence.models.aste import ASTA_TYPES
    from fantabot.config import harvest_dir

    if asta_type not in ASTA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}",
        )

    args = ["harvest", "load", "--asta-type", asta_type]
    if follow:
        args.append("--follow")
    job = processes.ProcessJob(
        processes.fantabot_command(*args),
        role=LOADER,
        landing=harvest_dir() / "live.jsonl",
    )
    # `stop` is a real callable for the first time: every job before this one was a daemon
    # thread, which cannot be interrupted from outside, and `JobRegistry.stop` answered
    # 409 rather than pretending. A supervised process can honestly answer yes.
    return JobStarted(job_id=registry.start(job.run, kind="harvest-load", stop=job.stop))


@router.post("/harvest/collect", response_model=JobStarted, tags=["harvest"])
def harvest_collect(pool: int = 0) -> JobStarted:
    """Subscribe to the live auctions in the seed and append every state to the landing zone.

    The irreplaceable one, and therefore last: the loader is idempotent and restartable
    and proved the supervisor first. A frame that never reached disk is gone, and an
    evening of auctions does not come back.

    **It refuses to start when `pool` is below the population, naming both numbers.** The
    bound is ours and on a live evening it is permanent: a watcher does not finish, so a
    queued auction never gets a permit and never connects at all. That cost 145 of 395
    auctions on 2026-08-27, silently, and today's seed is 1,705 rows against a
    `DEFAULT_POOL` of 1,000. The CLI warns and continues, which is right at a terminal
    where someone reads the warning; from a browser, at 21:00, it is a three-hour run that
    quietly follows two thirds of an evening.

    **No format selector, and none is reachable.** `from_seed_row` reads each row's own
    `asta_type`, so one seed carries both — a selector here would be the collection-time
    filter again, wearing the name of a convenience.
    """
    from fantabot.adapters.files.lock import COLLECTOR
    from fantabot.application.harvest_supervisor import DEFAULT_POOL
    from fantabot.config import harvest_dir

    home = harvest_dir()
    seed = read_seed(home / "seed.json")
    if not seed.exists:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no seed at {seed.path}. Scan first — a collect with an empty registry "
                "follows no auction for three hours and looks like a quiet night."
            ),
        )
    if not seed.ok:
        raise HTTPException(status_code=400, detail=f"the seed could not be read: {seed.error}")

    limit = pool or DEFAULT_POOL
    if limit < seed.rows:
        raise HTTPException(
            status_code=400,
            detail=(
                f"pool is {limit} and the seed holds {seed.rows} auction(s): "
                f"{seed.rows - limit} would wait for a slot that a live evening never "
                f"frees. Raise pool to at least {seed.rows}."
            ),
        )

    job = processes.ProcessJob(
        processes.fantabot_command("harvest", "collect", "--pool", str(limit)),
        role=COLLECTOR,
        landing=home / "live.jsonl",
    )
    return JobStarted(job_id=registry.start(job.run, kind="harvest-collect", stop=job.stop))

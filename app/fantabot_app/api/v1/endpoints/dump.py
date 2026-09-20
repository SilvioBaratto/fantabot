"""`db dump` — the one command in this phase whose deliverable is a path (T26).

**The rule this module exists to keep.** `tasks/archive/parity-spec.md` §8 Never #4: *no browser download of
a database dump*. The dump carries the `league_tokens` rows — encrypted, but still
credentials — and handing over the bytes puts the file wherever the browser puts
downloads, a directory covered by neither `application/db_dump.py`'s `/Volumes/` refusal
nor `.gitignore`'s `*.dump`. Both guards are about *where the file is*, so a route that
streams it has defeated them without touching either. The route names the path instead.

**Nothing here decides where that path is.** `dump_target` does, and the command asks
the same function — a path named by a surface that did not derive it is how a screen
comes to point at a file that is somewhere else.

**The GET is not a convenience.** A dump of this database is 1893 MB and minutes, and
the refusal it can hit is structural rather than transient: if `$HOME` is on the volume
the dump exists to survive the loss of, no dump is possible at all. Discovering that on
click, after a run, is the failure the guard is for. So the page asks first, and a
refused target offers **no path** rather than a greyed-out one — there is no path.

**A supervised child**, `endpoints/scrape.py`'s case and more of it: that one is minutes
of polite GETs, this is minutes of streaming ~2 GB. `endpoints/teams.py` states the
other side of the line — one small GET plus an insert answers in its own request.

**Stoppable, and only honestly so.** `ProcessJob` refuses a job with neither a landing
zone nor a flag, so offering the stop is not optional; what made it safe is
`application/db_dump.run_dump` removing a dump that did not finish. A `SIGINT` lands in
the middle of a stream, and a half-written file at the path this route names is worse
than none — it is the right size to look real and is refused by `pg_restore` only at the
moment the disk it was protecting against is already gone.

**Kept out of `endpoints/db.py`** for `endpoints/teams.py`'s reason: that module's
subject is the health probe, whose "must never 500" rule is the opposite of the one a
write needs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from fantabot.application.db_dump import DumpRefused, dump_target
from fastapi import APIRouter
from pydantic import BaseModel

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry

router = APIRouter()

#: `POST /db/dump`. Two, and the second is not an error the operator caused: `refused`
#: means this machine cannot hold a dump anywhere safe, which is a sentence about `$HOME`
#: and not about the request — there are no fields in it to get wrong.
DUMP_OUTCOMES = ("started", "refused")


def _today() -> date:
    """The one calendar read on this path, so a test freezes one thing.

    UTC rather than the local date `endpoints/scrape.py` reads, because the CLI's dump
    does: the filename is the only thing that distinguishes two dumps, and the two
    surfaces disagreeing about which day it is would have the app name a file the
    command then does not write.
    """
    return datetime.now(UTC).date()


def dump_flag() -> Path:
    """`~/.fantabot/dump.stop` — one flag, because there is one dump.

    A dump takes no landing-zone role, so `ProcessJob` needs a flag of its own and
    `stop_path` refuses any role but `collector` and `loader`. Undivided, unlike
    `scrape_flag`: two scrapes of different tables can run at once and must not share a
    stop, whereas two dumps would be two writes to one filename.
    """
    from fantabot_app import paths

    return paths.home() / "dump.stop"


class DumpTarget(BaseModel):
    """Where today's dump would land, or why nowhere would do."""

    #: Empty exactly when `refused` is not. Not `null`: a page that renders a path has
    #: one string to render, and the refusal is what it renders instead.
    path: str = ""
    #: The command's own sentence, so the operator reading it here and the one reading it
    #: in a terminal are reading the same words.
    refused: str = ""
    #: Whether today's dump has already been taken. A second one overwrites the first —
    #: one file per day is deliberate, since the failure guarded against is a dead disk
    #: and not an edit five minutes ago — so the screen has to say what is being replaced.
    exists: bool = False
    #: `null` rather than `0` when there is no file. A `0` is a claim about a dump that
    #: exists and is empty, which is the one dump worth panicking about.
    size_bytes: int | None = None


class DumpStarted(BaseModel):
    """The job, and the path it will write — never the bytes."""

    outcome: str
    path: str = ""
    job_id: str = ""
    detail: str = ""


@router.get("/db/dump/target", response_model=DumpTarget, tags=["system"])
def dump_target_route() -> DumpTarget:
    """Where the dump would go, asked before committing minutes to finding out."""
    try:
        target = dump_target(Path.home(), _today())
    except DumpRefused as refused:
        return DumpTarget(refused=str(refused))

    exists = target.exists()
    return DumpTarget(
        path=str(target),
        exists=exists,
        size_bytes=target.stat().st_size if exists else None,
    )


@router.post("/db/dump", response_model=DumpStarted, tags=["system"])
def dump_run() -> DumpStarted:
    """Start `fantabot db dump`, and say where it will land — `tasks/archive/parity-spec.md` §8 Never #4.

    The path is derived here and again by the child, from the same function: this route
    is what the operator reads, and a path it invented would be one the command does not
    write. The refusal is taken before the child exists, so a machine that cannot hold a
    dump safely spawns nothing rather than spending minutes discovering it.
    """
    try:
        target = dump_target(Path.home(), _today())
    except DumpRefused as refused:
        return DumpStarted(outcome="refused", detail=str(refused))

    job = processes.ProcessJob(processes.fantabot_command("db", "dump"), flag=dump_flag())
    job_id = registry.start(job.run, kind="db-dump", stop=job.stop)
    return DumpStarted(outcome="started", path=str(target), job_id=job_id)

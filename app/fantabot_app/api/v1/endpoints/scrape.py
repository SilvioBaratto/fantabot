"""`db scrape` — the longest write the app can start, and the picker that arms it (T23).

**A supervised child, not a request.** Three seasons of `voti` is ~114 GETs a second
apart plus parsing, which is `infrastructure/processes.py`'s case rather than
`endpoints/teams.py`'s: that module states the other side of the line — one small GET
plus an insert answers in its own request — and named this as what it is not.

**The picker exists because of a stale default, and the default is reported rather than
patched.** `voti.DEFAULT_SEASONS` and `statistiche.DEFAULT_SEASONS` stop at 2025/26
while 2026/27 is being played, so `fantabot db scrape voti` scrapes last season and
reports success, with nothing on the terminal to say so. `GET /db/scrape/tables` carries
each scraper's own list, whether it reaches the season being played, and which season
that comparison was made against — so the form can default to the season being played
instead of inheriting a list that is a year behind.

**Seasons are always sent explicitly.** `clean_scrape` resolves an empty list to the
scraper's own default *before* the child is spawned, so the argv in the job log is the
record of what ran. A bare `db scrape voti` in a log is indistinguishable between the
four seasons that happened and the one the operator meant.

**Kept out of `endpoints/db.py`** for `endpoints/teams.py`'s reason: that module's
subject is the health probe, whose "must never 500" rule is the opposite of the one a
write needs. The refusals here *are* refusals — a table that is not scrapable and a
season that is not a season — so they are `HTTPException`s, which is
`endpoints/exclusions.py`'s idiom and not `endpoints/room.py`'s named outcomes: nothing
below is about a credential, a network or the state of the database.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fantabot.application.scrape import (
    InvalidScrape,
    clean_scrape,
    current_season,
    scrapables,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fantabot_app.api.infrastructure import processes
from fantabot_app.api.infrastructure.jobs import registry

router = APIRouter()


def _today() -> date:
    """The one calendar read on this path, so a test freezes one thing.

    `endpoints/asta.py::_today`'s reason, applied to a much smaller stake: every
    assertion about which defaults are stale would otherwise move each August, and a
    picker whose contents change with the date is a picker no test can pin.
    """
    return date.today()  # noqa: DTZ011 — a local date, exactly as the CLI's seam reads it


def scrape_flag(table: str) -> Path:
    """`~/.fantabot/scrape.<table>.stop` — one flag per table, derived from it.

    A scrape takes no landing-zone role, so `ProcessJob` needs a flag of its own, and
    `stop_path` refuses any role but `collector` and `loader` — rightly: those two are a
    contract about who may hold a landing zone. Derived from the table for `stop_path`'s
    own reason, though: `quotazioni` and `voti` can run at once, and a shared flag would
    let a stop aimed at either stop the other.

    Under `~/.fantabot` rather than beside a landing zone because a scrape has no input
    file to sit next to. `request_stop` creates the directory, so nothing here has to.
    """
    from fantabot_app import paths

    return paths.home() / f"scrape.{table}.stop"


class ScrapeTable(BaseModel):
    """One table a scrape may be pointed at, as the form needs to see it."""

    table: str
    #: The rows it upserts. So the operator knows what a run touches before it runs.
    writes: list[str]
    #: What must already have been scraped. `players` and `teams` have no outbound
    #: foreign keys and everything else points at them, so on a fresh database anything
    #: but `quotazioni` first is a violation rather than a slow run.
    requires: list[str]
    #: The scraper's own `DEFAULT_SEASONS`, read live. What omitting `--season` does.
    default_seasons: list[str]
    #: Whether that list misses the season being played. Two of the three do.
    default_is_stale: bool


class ScrapeTables(BaseModel):
    """The picker's contents, and the season the staleness was measured against.

    `current_season` is on the response rather than left for the page to work out. The
    page would need a calendar to do it, which is a second clock on a second machine —
    a browser in another timezone would disagree with the server about which season is
    being played, and the disagreement would show up as a form default nobody chose.
    """

    current_season: str
    tables: list[ScrapeTable]


@router.get("/db/scrape/tables", response_model=ScrapeTables, tags=["system"])
def scrape_tables() -> ScrapeTables:
    """What may be scraped, in the order a fresh database needs them run.

    `application/scrape.scrapables` decides it, not this route: a picker whose idea of a
    scrapable table differs from the command's offers one the child then exits 2 on.
    """
    now = current_season(_today())
    return ScrapeTables(
        current_season=now,
        tables=[
            ScrapeTable(
                table=s.table,
                writes=list(s.writes),
                requires=list(s.requires),
                default_seasons=list(s.default_seasons),
                default_is_stale=s.default_is_stale,
            )
            for s in scrapables(now)
        ],
    )


class ScrapeRequest(BaseModel):
    """Which table, and which seasons.

    Empty means the scraper's own default, which is what the command does — parity, and
    deliberately still reachable even though the form never sends it: §8 Never #4 gives
    the app no power the CLI lacks and this route no restriction it has not got. What the
    form does instead is default the field to the season being played, so the stale list
    is something an operator chooses rather than something they inherit.
    """

    table: str
    seasons: list[str] = []


class JobStarted(BaseModel):
    job_id: str


@router.post("/db/scrape", response_model=JobStarted, tags=["system"])
def scrape_run(request: ScrapeRequest) -> JobStarted:
    """Fetch from fantacalcio.it and upsert, supervised as a child process.

    Stoppable, and safely so: every write is an upsert, `voti` commits per giornata and
    the site is still there, so a stop costs fetch time and never a row. That is the
    opposite of `harvest collect`, where a frame that never reached disk is gone — which
    is why this one needs no arming lock either.

    The table and the seasons go through `clean_scrape` — the command's own refusals, not
    a second copy of them — so a typo'd season is refused here in one sentence rather than
    38 giornate of 404s and backoff later.
    """
    try:
        cleaned = clean_scrape(request.table, request.seasons)
    except InvalidScrape as refused:
        raise HTTPException(status_code=400, detail=str(refused)) from None

    args = ["db", "scrape", cleaned.table]
    for season in cleaned.seasons:
        args += ["--season", season]
    job = processes.ProcessJob(
        processes.fantabot_command(*args), flag=scrape_flag(cleaned.table)
    )
    return JobStarted(job_id=registry.start(job.run, kind="db-scrape", stop=job.stop))

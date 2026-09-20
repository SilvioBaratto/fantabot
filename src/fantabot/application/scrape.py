"""What `db scrape` may be asked for, and the run itself (T23).

`adapters/scraping/` holds three scrapers that fetch a live site and upsert. This module
is the layer above them: which tables are scrapable, what a season is, what "no
`--season`" resolves to, and the enumeration a picker needs. All of it lived inside
`db_scrape`'s Typer body, where the app could not reach any of it.

**The body validated the table and nothing else.** `--season 2022/26` is well-formed and
has no page: `giornata_url` builds `.../2022-26/1`, the site 404s, `fetch_html` retries
three times with a 2 s and then 4 s backoff, and only then does the run fail — per
giornata, for 38 giornate. A typo in a season is not a slow failure worth measuring, so
both surfaces refuse it here, in one sentence, before a socket is opened.

**The stale default is the reason this task exists, and it is reported rather than
patched.** `voti.DEFAULT_SEASONS` and `statistiche.DEFAULT_SEASONS` stop at 2025/26 —
correct when they were written, and wrong now that 2026/27 is being played — so a run
that omits `--season` scrapes last season and reports success. `scrapables` reads each
scraper's **own** list rather than keeping a copy here: a copy would be a second default
to keep in step, which is the defect and not a report of it. Fix a scraper's list and
`default_is_stale` goes false with no edit to this module.

**The season being played is derived, not pinned.** A `CURRENT_SEASON = "2026/27"`
constant is the same disease as the default it is meant to detect — it is right until
next August and then silently wrong, which is exactly how `voti.DEFAULT_SEASONS` got
here. `current_season` takes the date rather than reading the clock, so the two callers
that need it have a seam and the tests are not a coin flip.

Nothing here opens a session: the scrapers hold their own, per season for `statistiche`
and per giornata for `voti`, which is what makes a killed run cheap to restart.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from types import ModuleType

#: `2026/27`. Anchored, so `x2026/27y` is not a season and neither is `2026/27 `-with-a-tab
#: that got past a `strip` this module did not do.
SEASON = re.compile(r"\A(\d{4})/(\d{2})\Z")

#: The month a new season's listone appears. July rather than August: the quotazioni for
#: the coming season publish before a ball is kicked, and that is when an operator wants
#: to scrape them. A `statistiche` or `voti` run against a season with no matches yet
#: prints `no player rows found — skipping` and moves on, which is survivable; a default
#: stuck a year behind is the trap this module exists to close.
SEASON_STARTS_IN_MONTH = 7

#: Which rows each scraper upserts, and what has to exist first. Structural rather than
#: prose so a screen can render the ordering without keeping its own copy of the sentence:
#: `players` and `teams` have no outbound foreign keys and everything else points at them,
#: so on a fresh database anything but `quotazioni` first is a foreign-key violation rather
#: than a slow run.
_TABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("quotazioni", ("quotazioni", "players", "teams"), ()),
    ("statistiche", ("statistiche",), ("quotazioni",)),
    ("voti", ("voti", "bonus_malus"), ("quotazioni",)),
)


class InvalidScrape(ValueError):
    """The scrape as asked for cannot be run, and nothing was fetched.

    Named so each surface refuses its own way — a `typer.Exit(2)` with the line printed,
    a 400 with the line in the body — without either having to tell this apart from a
    site being down. Raised before any request is made, which is what makes the message
    the whole answer.
    """


@dataclass(frozen=True)
class Scrapable:
    """One table a scrape may be pointed at, as a picker needs to see it."""

    table: str
    #: The rows it upserts. Rendered so the operator knows what a run touches.
    writes: tuple[str, ...]
    #: What must have been scraped first, on a fresh database.
    requires: tuple[str, ...]
    #: The scraper's own `DEFAULT_SEASONS`, read live. What a run with no `--season` does.
    default_seasons: tuple[str, ...]
    #: The season that comparison was made against, so a reader knows what "stale" means.
    measured_against: str

    @property
    def default_is_stale(self) -> bool:
        """Whether omitting `--season` would miss the season being played.

        The whole of the trap, as one boolean. A screen that renders it does not have to
        know which two of the three are behind, and stops saying so on the day they are not.
        """
        return self.measured_against not in self.default_seasons


@dataclass(frozen=True)
class ScrapeRequest:
    """A table and the seasons that will actually be fetched — never an empty tuple.

    "No `--season`" is resolved to the scraper's own default *here* rather than left for
    the scraper's own signature to supply, because both surfaces have to say what ran:
    a job log reading `scraping voti` is indistinguishable between the four seasons that
    happened and the one the operator meant.
    """

    table: str
    seasons: tuple[str, ...]


def current_season(today: date) -> str:
    """`2026/27` for any date from July 2026 to June 2027.

    The date is a parameter for `domain/asta/sentiment.py`'s reason: a module that reads
    the clock has tests that are a coin flip. Each surface reads it in one named place.
    """
    start = today.year if today.month >= SEASON_STARTS_IN_MONTH else today.year - 1
    return f"{start}/{(start + 1) % 100:02d}"


def scrapables(measured_against: str) -> tuple[Scrapable, ...]:
    """The three, in the order a fresh database needs them run.

    `measured_against` is passed in rather than derived so this stays clock-free and so a
    caller can ask what was stale on some other day — which is what the tests do instead
    of moving the calendar.
    """
    return tuple(
        Scrapable(
            table=table,
            writes=writes,
            requires=requires,
            default_seasons=tuple(_module(table).DEFAULT_SEASONS),
            measured_against=measured_against,
        )
        for table, writes, requires in _TABLES
    )


def clean_scrape(table: str, seasons: Sequence[str]) -> ScrapeRequest:
    """Refuse everything that can be refused without a socket, and resolve the default."""
    known = [name for name, _writes, _requires in _TABLES]
    if table not in known:
        raise InvalidScrape(
            f"{table!r} is not scrapable. Pick one of {', '.join(known)}."
        )

    asked = [clean_season(raw) for raw in seasons]
    # Ordered rather than a set: the operator's order is the order the site is walked, and
    # a scrape is minutes per season. Asking for one twice costs that time and, because
    # every write is an upsert, changes nothing.
    deduped = list(dict.fromkeys(asked))
    return ScrapeRequest(
        table=table,
        seasons=tuple(deduped) if deduped else tuple(_module(table).DEFAULT_SEASONS),
    )


def clean_season(raw: str) -> str:
    """`2026/27`, or the reason it is not one.

    Both halves are checked. The shape alone accepts `2022/26`, which the site has no page
    for and answers with three retries and a backoff — a refusal worth having before the
    first request rather than 30 s into it.
    """
    season = raw.strip()
    match = SEASON.match(season)
    if match is None:
        raise InvalidScrape(
            f"{raw!r} is not a season. Seasons are written like 2026/27."
        )
    start, end = int(match.group(1)), int(match.group(2))
    # Modulo, not `start + 1`: 2099/00 is a real pair and refusing it would be arithmetic
    # rather than a rule about football.
    if (start + 1) % 100 != end:
        raise InvalidScrape(
            f"{season!r} is not a season: it spans more than one year. Did you mean "
            f"{start}/{(start + 1) % 100:02d}?"
        )
    return season


def run_scrape(request: ScrapeRequest) -> None:
    """Fetch and upsert. Long, polite, and the only thing here that opens a socket."""
    _module(request.table).run(list(request.seasons))


def _module(table: str) -> ModuleType:
    """The scraper for a table, imported on use.

    Inside the function for `tests/test_db_boundary.py`'s reason: these modules pull in
    the whole persistence stack, and importing the CLI must load neither sqlalchemy nor
    playwright. The Typer body had the same import in the same place.
    """
    from importlib import import_module

    return import_module(f"fantabot.adapters.scraping.{table}")

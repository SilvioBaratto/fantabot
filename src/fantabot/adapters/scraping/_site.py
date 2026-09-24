"""What the three scrapers know about `fantacalcio.it` itself, in one place.

`quotazioni.py`, `statistiche.py` and `voti.py` read three different pages of one site.
The *pages* differ and their parsers rightly differ with them; what did not differ, and
was written out three times anyway, is everything about the **site**: how we identify
ourselves to it, how long we wait between requests, how long we wait for one, how many
times we try again, how it spells a player's id inside a link, and what its two role
vocabularies mean. Those are decisions, not presentation — the class of duplication
CLAUDE.md says to collapse, because an edit to one copy and not the others is silent.

Measured 2026-09-24 before the move, with `diff`: `fetch_html` was **byte-identical**
between `statistiche.py` and `voti.py`, `MAX_RETRIES` and its backoff included;
`CLASSIC_ROLES` and `MANTRA_ROLES` were byte-identical between `quotazioni.py` and
`statistiche.py`; `USER_AGENT`, `REQUEST_DELAY_SECONDS` and the `timeout=30` were each
written three times; the last-numeric-path-segment id rule was written three times and
explained in only one of them, which is the drift signature exactly.

**Four things stay where they are, on purpose.**

`DEFAULT_SEASONS` stays three separate lists. `application/scrape.py` reads each
scraper's **own** list live and compares it against a derived current season, and
CLAUDE.md records that this is what made the 2026-09-20 staleness fix one line per
scraper with nothing else edited. A copy here would be a second default to keep in step,
which is the defect and not a report of it, and it would also take away the test that
shortens one scraper's list and watches the flag flip.

`BASE_URL` stays three separate constants: three different pages, three different URLs,
and there is nothing shared to share.

`voti.ROLE_LABELS` stays in `voti.py` although it is `CLASSIC_ROLES` plus one entry.
It answers a different question: a *voti* page grades a coach (`all` -> `Allenatore`)
and a *listone* has no such role, so they agree today by coincidence of four letters and
not by construction. Deriving one from the other would make a future edit to the
listone's four roles silently rewrite what a coach is called.

`fetch_once` vs `fetch_html` is the one difference here that is real, and it is now
stated instead of being a shape you would have to `diff` to see. See `fetch_once`.

**The parsers themselves were left alone**, and that is a judgment rather than an
omission. Measured on the bodies of the three `handle_starttag` methods: `quotazioni` and
`statistiche` share 0.71 of their lines, and `voti` shares 0.29 with either — it reads no
`player-row` and no `data-col-key` at all, but `li.team-table`, `div.player-item.cell`,
pill indices and bonus spans. The one decision all three did share was the id rule, and
it is `player_id_from_href` now. What is left is two parsers that read two tables with
different columns into different dataclasses, plus a byte-identical eight-line
`handle_endtag`; a base class over that would need three hooks to save eight lines and
would make each parser a file-and-a-half to read, which this repository does not count as
a simplification.

Nothing in this module imports anything of ours: it is the site, and the site does not
know about our layers.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

#: How we introduce ourselves to `fantacalcio.it`. A real desktop Chrome string: the site
#: serves the tables we parse to a browser and something else to an obvious bot, so this is
#: load-bearing rather than cosmetic, and it is one string because the day it stops working
#: it stops working for all three pages at once.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: Politeness. One second between consecutive requests to the same host, applied by each
#: scraper's own loop — `voti` alone makes 38 of them per season. This is the number that
#: decides how hard a third party is hit, so it is one number.
REQUEST_DELAY_SECONDS = 1.0

#: Seconds to wait for one response before giving up on it.
TIMEOUT_SECONDS = 30

#: Attempts, not re-tries: `MAX_RETRIES = 3` is three GETs in total.
MAX_RETRIES = 3

#: Backoff step. Attempt *n* sleeps `BACKOFF_STEP_SECONDS * n`, so 3 attempts wait 2 s and
#: then 4 s. An int, because that is what the two copies this replaces slept.
BACKOFF_STEP_SECONDS = 2

#: The Classic listone's four role codes, as the site's `data-filter-role-classic`
#: attribute spells them, mapped to the label it prints. Lowercase keys: the attribute is
#: lowercase and the *database* column is uppercased at the call site, not here.
#: Unrelated to `domain/classic/roles.CLASSIC_ROLES`, which is a frozenset of the
#: uppercase codes and answers a legality question rather than a labelling one.
CLASSIC_ROLES = {
    "p": "Portiere",
    "d": "Difensore",
    "c": "Centrocampista",
    "a": "Attaccante",
}

#: The twelve Mantra role codes, from `data-filter-role-mantra`, mapped to their labels.
#: Note `c` is in both tables and means different things — Centrocampista in Classic,
#: Cen.centrale in Mantra — which is the same collision CLAUDE.md records for
#: `custom-roles`, and the reason these are two tables and not one.
MANTRA_ROLES = {
    "por": "Portiere",
    "dc": "Dif. centrale",
    "b": "Braccetto",
    "dd": "Dif. destro",
    "ds": "Dif. sinistro",
    "e": "Esterno",
    "m": "Mediano",
    "c": "Cen.centrale",
    "w": "Ala",
    "t": "Trequartista",
    "a": "Attaccante",
    "pc": "Punta centrale",
}


def player_id_from_href(href: str | None) -> str:
    """The player id inside a `player-name` link, or `""` when the link carries none.

    The id is the **last** numeric path segment, not the last segment: a current-season
    link ends `.../slug/<id>` and a past-season one ends `.../slug/<id>/2022-23`, so
    taking the tail outright reads the season as the id on every archived page — which is
    every page but one, and `quotazioni`'s own fixture is a past season.

    `""` for a link with no digits anywhere is a real answer and not an error. `voti`
    renders a coach as a bare `<span class="player-name">` with no href at all, and
    `to_payloads` stores `player_id=None` for exactly those rows; the caller decides.
    """
    for part in reversed((href or "").rstrip("/").split("/")):
        if part.isdigit():
            return part
    return ""


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _read(req: urllib.request.Request) -> str:
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        body: bytes = resp.read()
    return body.decode("utf-8")


def fetch_once(url: str) -> str:
    """One GET, one chance: whatever it raises, the caller sees immediately.

    This is `quotazioni`'s fetch, and the asymmetry with `fetch_html` is **deliberate and
    unmeasured** — recorded here rather than left as a shape you would have to `diff` two
    files to notice. The argument for it: a listone run is one request per season where a
    `voti` season is 38 and a `statistiche` season is 3, so a transient failure costs a
    retyped command rather than half an hour, and an operator watching a single GET fail
    is better served by the error than by 6 seconds of silence.

    The argument against it is just as short: one request is also the cheapest thing in
    the repo to retry, and `run` turns "no rows" into `SystemExit(1)`, so a blip reads as
    a changed page. **Nobody has measured which is right**, and giving `quotazioni` the
    retry would change behaviour, so it has not been done from inside a refactor. If it
    is ever decided, the whole change is `fetch_once` -> `fetch_html` in `quotazioni.py`.
    """
    return _read(_request(url))


def fetch_html(url: str) -> str:
    """Up to `MAX_RETRIES` GETs, sleeping 2 s then 4 s, then the last failure re-raised.

    `statistiche` and `voti` sweep a season's worth of pages in one run, so a single
    refused connection would otherwise cost the whole sweep. Only `URLError` and
    `TimeoutError` are retried — and `HTTPError` is a `URLError`, so a 404 costs three
    attempts and 6 seconds, which is why `application/scrape.py` refuses a malformed
    `--season` before a socket is opened rather than letting the site answer it.

    The `Request` object is built once and reused across attempts, as it was in both
    copies this replaces.
    """
    req = _request(url)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _read(req)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_STEP_SECONDS * attempt)
    assert last_error is not None
    raise last_error

"""Scrape official player quotazioni from fantacalcio.it, all seasons, into Postgres.

Source: https://www.fantacalcio.it/quotazioni-fantacalcio[/YYYY-YY] — server-renders
the full player table (~500-700 rows) in one plain GET per season, no API/JS
required (verified via network capture: pagination clicks and the season
dropdown both resolve to a full page navigation, zero XHR). Each <tr
class="player-row"> carries the data as element attributes/text; parsed here
with stdlib html.parser (no BeautifulSoup dependency needed).

Usage:
    fantabot db scrape quotazioni [--season 2022/23 --season 2023/24 ...]

Default seasons: 2022/23 through 2026/27 (current).

Upserts, tagged by "stagione" and "listone" so both role systems share a table:
    quotazioni  — one row per player per season per listone (classic, mantra)
    players     — id and name, so the foreign keys resolve
    teams       — the three-letter code; the full name is filled in afterwards
                  by _db.resolve_team_names_or_report()

This must run before scrape_voti and scrape_statistiche on a fresh database:
it is the only scraper that writes players and teams, and the others point at
them.

The GET retries, like the other two scrapers': `_site.fetch_html`, three attempts
with a 2 s then 4 s backoff. It did not until **2026-09-24**, when the asymmetry was
removed deliberately — `_site.fetch_html` holds both arguments, so that the shape is
not "restored" by someone who has only met one of them.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

from fantabot.adapters.persistence import scraping as _db
from fantabot.adapters.scraping._site import (
    CLASSIC_ROLES,
    MANTRA_ROLES,
    REQUEST_DELAY_SECONDS,
    fetch_html,
    player_id_from_href,
)

BASE_URL = "https://www.fantacalcio.it/quotazioni-fantacalcio"
# Every season to scrape, not a pointer at the current one. Deliberately *not* in `_site`
# with the rest of the shared constants: `application/scrape.py` reads each scraper's own
# list live, which is what made the 2026-09-20 staleness fix one line per scraper. See the
# `_site` docstring.
DEFAULT_SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26", "2026/27"]


@dataclass
class PlayerRow:
    season: str
    player_id: str
    name: str
    team: str
    role_classic_code: str
    role_mantra_codes: list[str] = field(default_factory=list)
    played_last_season: str = "0"
    c_qi: str = ""
    c_qa: str = ""
    c_fvm: str = ""
    m_qi: str = ""
    m_qa: str = ""
    m_fvm: str = ""

    @property
    def role_classic_label(self) -> str:
        return CLASSIC_ROLES.get(self.role_classic_code, self.role_classic_code)

    @property
    def role_mantra_labels(self) -> list[str]:
        return [MANTRA_ROLES.get(code, code) for code in self.role_mantra_codes]


class QuotazioniParser(HTMLParser):
    """Extracts one PlayerRow per <tr class="player-row"> in the quotazioni table."""

    def __init__(self, season: str) -> None:
        super().__init__()
        self.season = season
        self.rows: list[PlayerRow] = []
        self._row: PlayerRow | None = None
        self._in_name_link = False
        self._capture_key: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = d.get("class") or ""

        if tag == "tr" and "player-row" in classes.split():
            self._row = PlayerRow(
                season=self.season,
                player_id="",
                name="",
                team="",
                role_classic_code=d.get("data-filter-role-classic") or "",
                role_mantra_codes=(d.get("data-filter-role-mantra") or "").split("|"),
                played_last_season=d.get("data-filter-playeds") or "0",
            )
            return

        if self._row is None:
            return

        if tag == "a" and "player-name" in classes.split():
            found = player_id_from_href(d.get("href"))
            if found:
                self._row.player_id = found
            self._in_name_link = True
            return

        if tag == "td":
            key = d.get("data-col-key")
            if key:
                self._capture_key = key
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_name_link = False
        elif tag == "td":
            self._capture_key = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._row is None:
            return
        text = data.strip()
        if not text:
            return

        if self._in_name_link:
            self._row.name = text
        elif self._capture_key == "sq":
            self._row.team = text
        elif self._capture_key in ("c_qi", "c_qa", "c_fvm", "m_qi", "m_qa", "m_fvm"):
            setattr(self._row, self._capture_key, text)


def season_url(season: str) -> str:
    return f"{BASE_URL}/{season.replace('/', '-')}"


def fetch_season(season: str) -> list[PlayerRow]:
    html = fetch_html(season_url(season))
    parser = QuotazioniParser(season)
    parser.feed(html)
    return parser.rows


def run(seasons: Sequence[str] = DEFAULT_SEASONS) -> None:
    """Fetch the listone for each season and upsert players, teams and quotazioni."""

    all_rows: list[PlayerRow] = []
    for i, season in enumerate(seasons):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        rows = fetch_season(season)
        if not rows:
            print(f"  {season}: no player rows found — skipping", file=sys.stderr)
            continue
        print(f"  {season}: {len(rows)} players")
        all_rows.extend(rows)

    if not all_rows:
        print("No player rows found for any season — page structure may have changed.", file=sys.stderr)
        raise SystemExit(1)

    payload: list[dict[str, object]] = []
    for r in all_rows:
        payload.append(
            {
                "stagione": r.season,
                "player_id": int(r.player_id),
                "nome": r.name,
                "listone": "classic",
                "squadra": r.team.upper(),
                "ruoli_codice": [r.role_classic_code.upper()],
                "ruoli": [r.role_classic_label],
                "qi": int(r.c_qi or 0),
                "qa": int(r.c_qa or 0),
                "fvm": int(r.c_fvm or 0),
            }
        )
        payload.append(
            {
                "stagione": r.season,
                "player_id": int(r.player_id),
                "nome": r.name,
                "listone": "mantra",
                "squadra": r.team.upper(),
                "ruoli_codice": [c.upper() for c in r.role_mantra_codes],
                "ruoli": r.role_mantra_labels,
                "qi": int(r.m_qi or 0),
                "qa": int(r.m_qa or 0),
                "fvm": int(r.m_fvm or 0),
            }
        )

    with _db.session() as handle:
        stored = _db.upsert_quotazioni(handle, payload)

    print(f"{len(all_rows)} players across {len(seasons)} seasons -> {stored} quotazioni rows")

    # A promoted club arrives here first, named after its own code by the
    # placeholder insert. Resolve it now if fixtures exist to resolve it from.
    _db.resolve_team_names_or_report()



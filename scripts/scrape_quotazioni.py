"""Scrape official player quotazioni from fantacalcio.it, all seasons, into two CSVs.

Source: https://www.fantacalcio.it/quotazioni-fantacalcio[/YYYY-YY] — server-renders
the full player table (~500-700 rows) in one plain GET per season, no API/JS
required (verified via network capture: pagination clicks and the season
dropdown both resolve to a full page navigation, zero XHR). Each <tr
class="player-row"> carries the data as element attributes/text; parsed here
with stdlib html.parser (no BeautifulSoup dependency needed).

Usage:
    python scripts/scrape_quotazioni.py [--out-dir data] [--seasons 2022/23 2023/24 ...]

Default seasons: 2022/23 through 2026/27 (current).

Writes (rows from every requested season stacked, tagged by "stagione"):
    <out-dir>/quotazioni_classic.csv
    <out-dir>/quotazioni_mantra.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://www.fantacalcio.it/quotazioni-fantacalcio"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DEFAULT_SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26", "2026/27"]
REQUEST_DELAY_SECONDS = 1.0

CLASSIC_ROLES = {
    "p": "Portiere",
    "d": "Difensore",
    "c": "Centrocampista",
    "a": "Attaccante",
}

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
            href = (d.get("href") or "").rstrip("/")
            # current season: .../slug/<id>; past seasons: .../slug/<id>/2022-23
            for part in reversed(href.split("/")):
                if part.isdigit():
                    self._row.player_id = part
                    break
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


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body: bytes = resp.read()
    return body.decode("utf-8")


def fetch_season(season: str) -> list[PlayerRow]:
    html = fetch_html(season_url(season))
    parser = QuotazioniParser(season)
    parser.feed(html)
    return parser.rows


def write_classic_csv(rows: list[PlayerRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["stagione", "id", "nome", "squadra", "ruolo_codice", "ruolo", "qi", "qa", "fvm"]
        )
        for r in sorted(rows, key=lambda r: (r.season, r.team, r.role_classic_code, r.name)):
            writer.writerow(
                [
                    r.season,
                    r.player_id,
                    r.name,
                    r.team,
                    r.role_classic_code,
                    r.role_classic_label,
                    r.c_qi,
                    r.c_qa,
                    r.c_fvm,
                ]
            )


def write_mantra_csv(rows: list[PlayerRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["stagione", "id", "nome", "squadra", "ruoli_codice", "ruoli", "qi", "qa", "fvm"]
        )
        for r in sorted(rows, key=lambda r: (r.season, r.team, r.role_mantra_codes, r.name)):
            writer.writerow(
                [
                    r.season,
                    r.player_id,
                    r.name,
                    r.team,
                    ";".join(code.upper() for code in r.role_mantra_codes),
                    ";".join(r.role_mantra_labels),
                    r.m_qi,
                    r.m_qa,
                    r.m_fvm,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("data"), help="Directory to write CSVs into"
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=DEFAULT_SEASONS,
        help="Seasons to fetch, e.g. --seasons 2022/23 2023/24 (default: %(default)s)",
    )
    args = parser.parse_args()

    all_rows: list[PlayerRow] = []
    for i, season in enumerate(args.seasons):
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

    args.out_dir.mkdir(parents=True, exist_ok=True)
    classic_path = args.out_dir / "quotazioni_classic.csv"
    mantra_path = args.out_dir / "quotazioni_mantra.csv"

    write_classic_csv(all_rows, classic_path)
    write_mantra_csv(all_rows, mantra_path)

    print(f"{len(all_rows)} total rows across {len(args.seasons)} seasons -> {classic_path}, {mantra_path}")


if __name__ == "__main__":
    main()

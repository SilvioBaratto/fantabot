"""Scrape official season-aggregate player statistics from fantacalcio.it into two CSVs.

Source: https://www.fantacalcio.it/statistiche-serie-a/{season}/{fonte} — server-
renders the full season-totals table (~650-700 rows) in one plain GET per
(season, fonte), no API/JS required (same SSR pattern as quotazioni/voti).
"fonte" (rating source/redazione) is a real server-side dataset — verified by
diffing the same player's numbers across all three (e.g. Immobile 2022/23:
29 games/6,22 avg via "fantacalcio" vs 30 games/6,17 via "statistico" vs
29 games/6,16 via "italia") — so it needs one fetch per source, unlike
classic/mantra which are embedded together in every fetch (each
<tr class="player-row"> carries both data-filter-role-classic and
data-filter-role-mantra, plus both role <th> columns, same as quotazioni).
This is season *totals*, not per-giornata — no matchday dimension here.

Columns per row: sq, pg (partite giocate), mv (media voto), mfv (media
fantavoto), gol, gs (gol subiti), rig ("X / Y" = rigori segnati/tirati, split
here), rp (rigori parati), ass (assist), amm (ammonizioni), esp (espulsioni).
Parsed with stdlib html.parser (no BeautifulSoup dependency needed).

Usage:
    python scripts/scrape_statistiche.py [--out-dir data] [--seasons 2022/23 ...] [--providers fantacalcio statistico italia]

Default seasons: 2022/23 through 2025/26 (2026/27 excluded — preseason, all
zeros as of writing; pass --seasons explicitly to include it).

Writes (rows from every season/fonte stacked together):
    <out-dir>/statistiche_classic.csv
    <out-dir>/statistiche_mantra.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _db  # noqa: E402
from fantabot.db.importers._csv import italian_decimal  # noqa: E402

BASE_URL = "https://www.fantacalcio.it/statistiche-serie-a"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DEFAULT_SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26"]
PROVIDERS = ["fantacalcio", "statistico", "italia"]
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3

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

STAT_COL_KEYS = ("sq", "pg", "mv", "mfv", "gol", "gs", "rig", "rp", "ass", "amm", "esp")


@dataclass
class PlayerStatsRow:
    season: str
    provider: str
    player_id: str = ""
    name: str = ""
    team: str = ""
    role_classic_code: str = ""
    role_mantra_codes: list[str] = field(default_factory=list)
    stats: dict[str, str] = field(default_factory=dict)

    @property
    def role_classic_label(self) -> str:
        return CLASSIC_ROLES.get(self.role_classic_code, self.role_classic_code)

    @property
    def role_mantra_labels(self) -> list[str]:
        return [MANTRA_ROLES.get(code, code) for code in self.role_mantra_codes]

    @property
    def rigori_segnati(self) -> str:
        return self._rig_part(0)

    @property
    def rigori_tirati(self) -> str:
        return self._rig_part(1)

    def _rig_part(self, index: int) -> str:
        raw = self.stats.get("rig", "")
        parts = [p.strip() for p in raw.split("/")]
        return parts[index] if len(parts) == 2 else ""


class StatisticheParser(HTMLParser):
    """Extracts one PlayerStatsRow per <tr class="player-row"> on a statistiche page."""

    def __init__(self, season: str, provider: str) -> None:
        super().__init__()
        self.season = season
        self.provider = provider
        self.rows: list[PlayerStatsRow] = []
        self._row: PlayerStatsRow | None = None
        self._in_name_link = False
        self._capture_key: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = (d.get("class") or "").split()

        if tag == "tr" and "player-row" in classes:
            self._row = PlayerStatsRow(
                season=self.season,
                provider=self.provider,
                role_classic_code=d.get("data-filter-role-classic") or "",
                role_mantra_codes=(d.get("data-filter-role-mantra") or "").split("|"),
            )
            return

        if self._row is None:
            return

        if tag == "a" and "player-name" in classes:
            href = (d.get("href") or "").rstrip("/")
            for part in reversed(href.split("/")):
                if part.isdigit():
                    self._row.player_id = part
                    break
            self._in_name_link = True
            return

        if tag == "td":
            key = d.get("data-col-key")
            if key in STAT_COL_KEYS:
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
        elif self._capture_key is not None:
            existing = self._row.stats.get(self._capture_key, "")
            self._row.stats[self._capture_key] = f"{existing} {text}".strip()


def stats_url(season: str, provider: str) -> str:
    return f"{BASE_URL}/{season.replace('/', '-')}/{provider}"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body: bytes = resp.read()
            return body.decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error


def fetch_provider(season: str, provider: str) -> list[PlayerStatsRow]:
    html = fetch_html(stats_url(season, provider))
    parser = StatisticheParser(season, provider)
    parser.feed(html)
    return parser.rows


def write_classic_csv(rows: list[PlayerStatsRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "stagione",
                "fonte",
                "id",
                "nome",
                "squadra",
                "ruolo_codice",
                "ruolo",
                "partite_giocate",
                "media_voto",
                "media_fantavoto",
                "gol",
                "gol_subiti",
                "rigori_segnati",
                "rigori_tirati",
                "rigori_parati",
                "assist",
                "ammonizioni",
                "espulsioni",
            ]
        )
        for r in sorted(
            rows, key=lambda r: (r.season, r.provider, r.team, r.role_classic_code, r.name)
        ):
            writer.writerow(
                [
                    r.season,
                    r.provider,
                    r.player_id,
                    r.name,
                    r.team,
                    r.role_classic_code,
                    r.role_classic_label,
                    r.stats.get("pg", ""),
                    r.stats.get("mv", ""),
                    r.stats.get("mfv", ""),
                    r.stats.get("gol", ""),
                    r.stats.get("gs", ""),
                    r.rigori_segnati,
                    r.rigori_tirati,
                    r.stats.get("rp", ""),
                    r.stats.get("ass", ""),
                    r.stats.get("amm", ""),
                    r.stats.get("esp", ""),
                ]
            )


def write_mantra_csv(rows: list[PlayerStatsRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "stagione",
                "fonte",
                "id",
                "nome",
                "squadra",
                "ruoli_codice",
                "ruoli",
                "partite_giocate",
                "media_voto",
                "media_fantavoto",
                "gol",
                "gol_subiti",
                "rigori_segnati",
                "rigori_tirati",
                "rigori_parati",
                "assist",
                "ammonizioni",
                "espulsioni",
            ]
        )
        for r in sorted(
            rows, key=lambda r: (r.season, r.provider, r.team, r.role_mantra_codes, r.name)
        ):
            writer.writerow(
                [
                    r.season,
                    r.provider,
                    r.player_id,
                    r.name,
                    r.team,
                    ";".join(code.upper() for code in r.role_mantra_codes),
                    ";".join(r.role_mantra_labels),
                    r.stats.get("pg", ""),
                    r.stats.get("mv", ""),
                    r.stats.get("mfv", ""),
                    r.stats.get("gol", ""),
                    r.stats.get("gs", ""),
                    r.rigori_segnati,
                    r.rigori_tirati,
                    r.stats.get("rp", ""),
                    r.stats.get("ass", ""),
                    r.stats.get("amm", ""),
                    r.stats.get("esp", ""),
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=DEFAULT_SEASONS,
        help="Seasons to fetch, e.g. --seasons 2022/23 2023/24 (default: %(default)s)",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=PROVIDERS,
        choices=PROVIDERS,
        help="Rating sources to fetch (default: %(default)s)",
    )
    args = parser.parse_args()

    all_rows: list[PlayerStatsRow] = []
    first = True
    for season in args.seasons:
        for provider in args.providers:
            if not first:
                time.sleep(REQUEST_DELAY_SECONDS)
            first = False
            rows = fetch_provider(season, provider)
            if not rows:
                print(f"  {season}/{provider}: no player rows found — skipping", file=sys.stderr)
                continue
            print(f"  {season}/{provider}: {len(rows)} players")
            all_rows.extend(rows)

    if not all_rows:
        print("No player rows found for any season/provider — page structure may have changed.", file=sys.stderr)
        raise SystemExit(1)

    def counter(raw: str) -> int:
        return int(raw) if raw.strip() else 0

    payload: list[dict[str, object]] = []
    for r in all_rows:
        if not r.player_id:
            continue
        base = {
            "stagione": r.season,
            "fonte": r.provider,
            "player_id": int(r.player_id),
            "nome": r.name,
            "squadra": r.team.upper(),
            "partite_giocate": counter(r.stats.get("pg", "")),
            "media_voto": italian_decimal(r.stats.get("mv", "")),
            "media_fantavoto": italian_decimal(r.stats.get("mfv", "")),
            "gol": counter(r.stats.get("gol", "")),
            "gol_subiti": counter(r.stats.get("gs", "")),
            "rigori_segnati": counter(r.rigori_segnati),
            "rigori_tirati": counter(r.rigori_tirati),
            "rigori_parati": counter(r.stats.get("rp", "")),
            "assist": counter(r.stats.get("ass", "")),
            "ammonizioni": counter(r.stats.get("amm", "")),
            "espulsioni": counter(r.stats.get("esp", "")),
        }
        payload.append(
            {
                **base,
                "listone": "classic",
                "ruoli_codice": [r.role_classic_code.upper()],
                "ruoli": [r.role_classic_label],
            }
        )
        payload.append(
            {
                **base,
                "listone": "mantra",
                "ruoli_codice": [c.upper() for c in r.role_mantra_codes],
                "ruoli": r.role_mantra_labels,
            }
        )

    with _db.session() as handle:
        stored = _db.upsert_statistiche(handle, payload)

    print(
        f"{len(all_rows)} scraped rows across {len(args.seasons)} seasons x "
        f"{len(args.providers)} providers -> {stored} statistiche rows"
    )


if __name__ == "__main__":
    main()

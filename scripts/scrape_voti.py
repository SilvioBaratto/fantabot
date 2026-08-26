"""Scrape official match-by-match player grades from fantacalcio.it into two CSVs.

Source: https://www.fantacalcio.it/voti-fantacalcio-serie-a/{season}/{giornata} —
server-renders every match of that giornata in one plain GET (verified: same
SSR pattern as quotazioni, no API/JS needed). For each giornata the page has
one <li class="team-table" id="team-{teamId}"> per team per match (20 per
giornata = 10 matches x 2 sides), each containing a <table class="grades-table">
of <div class="player-item cell"> rows. Each row carries:
  - 3 vote sources (Redazione Fantacalcio, Voto Statistico, Voto Italia), each
    a Voto (V) + FantaVoto (FV) pair
  - 8 bonus/malus stats (data-key/title on each <span class="player-bonus">):
    scoredGoals, concededGoals, ownGoals, scoredPenalties, missedPenalties,
    savedPenalties, assists, manOfTheMatch
  - yellow/red card, encoded as a CSS class on the grade span rather than a
    bonus stat (ammonizione/espulsione are real malus categories in
    fantacalcio scoring, so they're pulled into bonus_malus.csv here even
    though the site doesn't list them among its 8 icon columns)
Confirmed identical markup/column set across 2022/23-2025/26. Parsed with
stdlib html.parser (no BeautifulSoup dependency needed).

Usage:
    python scripts/scrape_voti.py [--out-dir data] [--seasons 2022/23 2023/24 ...]

Default seasons: 2022/23 through 2025/26. Giornate per season are discovered
from that season's "Giornata" <select> (38 for a 20-team Serie A season, but
not hardcoded).

Writes (rows from every season/giornata/match/player stacked together):
    <out-dir>/voti.csv         — Voto e FantaVoto (3 sources)
    <out-dir>/bonus_malus.csv  — Bonus e Malus (8 stats + cards)
"""

from __future__ import annotations

import argparse
import csv
import re
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
from fantabot.db.importers.matches import parse_date, parse_time  # noqa: E402

BASE_URL = "https://www.fantacalcio.it/voti-fantacalcio-serie-a"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DEFAULT_SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26"]
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3

ROLE_LABELS = {
    "p": "Portiere",
    "d": "Difensore",
    "c": "Centrocampista",
    "a": "Attaccante",
    "all": "Allenatore",
}

BONUS_TITLE_TO_KEY = {
    "Gol segnati": "gol_segnati",
    "Gol subiti": "gol_subiti",
    "Autoreti": "autoreti",
    "Rigori segnati": "rigori_segnati",
    "Rigori sbagliati": "rigori_sbagliati",
    "Rigori parati": "rigori_parati",
    "Assist": "assist",
    "Player of the match": "mvp",
}
BONUS_KEYS = list(BONUS_TITLE_TO_KEY.values())

GIORNATA_OPTION_RE = re.compile(r'<option value="(\d+)"[^>]*>Giornata \d+</option>')
BARE_TWO_DIGIT_RE = re.compile(r"^(-?)(\d)(\d)$")
# Real fantavoto whole numbers (no comma) observed in the wild top out at ~21
# (grade + goal + assist + MVP stacked); a bare value >= 30 can't be a real
# score, so it's this glitch instead.
FANTAVOTO_GLITCH_THRESHOLD = 30


def normalize_grade(raw: str, *, is_base_voto: bool) -> str:
    """Fix a fantacalcio.it markup glitch: on some "subentrato" rows the grade's
    decimal comma is dropped, so e.g. "5,5" renders as bare "55" (site-side bug,
    confirmed by comparing against the live page — not a parsing artifact here).

    Base voto (before bonus/malus) never legitimately reaches two digits, so any
    bare 2-digit voto is unambiguously this glitch. FantaVoto legitimately does
    reach two digits via bonus stacking (e.g. "10", "21"), so only values that
    could never be a real score (>= 30) are treated as the glitch there.
    """
    m = BARE_TWO_DIGIT_RE.match(raw)
    if not m:
        return raw
    sign, tens, ones = m.groups()
    if is_base_voto or int(tens + ones) >= FANTAVOTO_GLITCH_THRESHOLD:
        return f"{sign}{tens},{ones}"
    return raw


@dataclass
class PlayerMatchRow:
    season: str
    giornata: int
    date: str = ""
    time: str = ""
    team: str = ""
    opponent: str = ""
    goals_for: str = ""
    goals_against: str = ""
    player_id: str = ""
    name: str = ""
    role_code: str = ""
    ammonizione: bool = False
    espulsione: bool = False
    voto_fc: str = ""
    fantavoto_fc: str = ""
    voto_stat: str = ""
    fantavoto_stat: str = ""
    voto_italia: str = ""
    fantavoto_italia: str = ""
    bonus: dict[str, str] = field(default_factory=dict)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role_code, self.role_code)


class GiornataParser(HTMLParser):
    """Extracts one PlayerMatchRow per player-per-team-table on a giornata page."""

    def __init__(self, season: str, giornata: int) -> None:
        super().__init__()
        self.season = season
        self.giornata = giornata
        self.rows: list[PlayerMatchRow] = []

        self._in_team_table = False
        self._match_team = ""
        self._match_opponent = ""
        self._match_gf = ""
        self._match_ga = ""
        self._match_date = ""
        self._match_time = ""

        self._in_match_score = False
        self._score_buf: list[str] = []
        self._in_match_date = False
        self._date_buf: list[str] = []

        self._row: PlayerMatchRow | None = None
        self._pill_idx = -1
        self._in_name_link = False

    def _flush_row(self) -> None:
        if self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = (d.get("class") or "").split()

        if tag == "li" and "team-table" in classes:
            self._flush_row()
            self._in_team_table = True
            self._match_team = self._match_opponent = ""
            self._match_gf = self._match_ga = ""
            self._match_date = self._match_time = ""
            return

        if not self._in_team_table:
            return

        if tag == "div" and classes == ["match-score"]:
            self._in_match_score = True
            self._score_buf = []
            return

        if tag == "div" and "match-date" in classes and "ml-auto" in classes:
            self._in_match_date = True
            self._date_buf = []
            return

        if tag == "div" and "player-item" in classes and "cell" in classes:
            self._flush_row()
            self._row = PlayerMatchRow(
                season=self.season,
                giornata=self.giornata,
                date=self._match_date,
                time=self._match_time,
                team=self._match_team,
                opponent=self._match_opponent,
                goals_for=self._match_gf,
                goals_against=self._match_ga,
            )
            self._pill_idx = -1
            return

        if self._row is None:
            return

        if tag == "span" and "role" in classes:
            self._row.role_code = d.get("data-value") or ""
            return

        if tag in ("a", "span") and "player-name" in classes:
            # players: <a class="player-name player-link" href=".../<id>[/<season>]">
            # coaches (role "all"): bare <span class="player-name"> Gasperini </span>, no id
            if tag == "a":
                href = (d.get("href") or "").rstrip("/")
                for part in reversed(href.split("/")):
                    if part.isdigit():
                        self._row.player_id = part
                        break
            self._in_name_link = True
            return

        if tag == "div" and classes == ["pill"]:
            self._pill_idx += 1
            return

        if tag == "span" and "player-grade" in classes:
            value = normalize_grade(d.get("data-value") or "", is_base_voto=True)
            if "yellow-card" in classes:
                self._row.ammonizione = True
            if "red-card" in classes:
                self._row.espulsione = True
            if self._pill_idx == 0:
                self._row.voto_fc = value
            elif self._pill_idx == 1:
                self._row.voto_stat = value
            elif self._pill_idx == 2:
                self._row.voto_italia = value
            return

        if tag == "span" and "player-fanta-grade" in classes:
            value = normalize_grade(d.get("data-value") or "", is_base_voto=False)
            if self._pill_idx == 0:
                self._row.fantavoto_fc = value
            elif self._pill_idx == 1:
                self._row.fantavoto_stat = value
            elif self._pill_idx == 2:
                self._row.fantavoto_italia = value
            return

        if tag == "span" and "player-bonus" in classes:
            key = BONUS_TITLE_TO_KEY.get(d.get("title") or "")
            if key:
                self._row.bonus[key] = d.get("data-value") or ""
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "span"):
            self._in_name_link = False
        elif tag == "div" and self._in_match_date:
            self._match_date, _, self._match_time = "".join(self._date_buf).strip().partition(
                " - "
            )
            self._in_match_date = False

    def handle_data(self, data: str) -> None:
        if self._in_match_score:
            text = data.strip()
            if text:
                self._score_buf.append(text)
                if len(self._score_buf) == 5:
                    self._match_team = self._score_buf[0]
                    self._match_gf = self._score_buf[1]
                    self._match_ga = self._score_buf[3]
                    self._match_opponent = self._score_buf[4]
                    self._in_match_score = False
            return

        if self._in_match_date:
            self._date_buf.append(data)
            return

        if self._in_name_link and self._row is not None:
            text = data.strip()
            if text:
                self._row.name = text


def giornata_url(season: str, giornata: int) -> str:
    return f"{BASE_URL}/{season.replace('/', '-')}/{giornata}"


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


def max_giornata(html: str) -> int:
    values = [int(m) for m in GIORNATA_OPTION_RE.findall(html)]
    if not values:
        raise ValueError("Could not find 'Giornata' options on page")
    return max(values)


def fetch_giornata(season: str, giornata: int, html: str | None = None) -> list[PlayerMatchRow]:
    html = html if html is not None else fetch_html(giornata_url(season, giornata))
    parser = GiornataParser(season, giornata)
    parser.feed(html)
    parser._flush_row()
    return parser.rows


def store_giornata(rows: list[PlayerMatchRow]) -> int:
    """Commit one matchday. Per-giornata rather than one batch at the end, so a
    run killed at giornata 30 keeps the first 29 and restarting is cheap — the
    upsert makes re-fetching a stored matchday a no-op."""
    if not rows:
        return 0
    voti, bonus = to_payloads(rows)
    with _db.session() as handle:
        _db.upsert_match_grain(handle, voti, bonus)
    return len(rows)


def fetch_season(season: str) -> int:
    first_html = fetch_html(giornata_url(season, 1))
    last_giornata = max_giornata(first_html)
    print(f"  {season}: {last_giornata} giornate")

    stored = store_giornata(fetch_giornata(season, 1, html=first_html))
    for g in range(2, last_giornata + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        g_rows = fetch_giornata(season, g)
        stored += store_giornata(g_rows)
        print(f"    giornata {g}/{last_giornata}: {len(g_rows)} player-rows")
    return stored


def to_payloads(rows: list[PlayerMatchRow]) -> tuple[list[dict], list[dict]]:
    """One scraped row becomes one voti row and one bonus_malus row."""

    def counter(raw: str) -> int:
        return int(raw) if str(raw).strip() else 0

    voti: list[dict] = []
    bonus: list[dict] = []
    for r in rows:
        player_id = int(r.player_id) if r.player_id else None
        shared = {
            "stagione": r.season,
            "giornata": r.giornata,
            "data": parse_date(r.date),
            "squadra_raw": r.team,
            "avversario_raw": r.opponent,
            "player_id": player_id,
            "nome": r.name,
            "ruolo_codice": r.role_code.upper(),
            "ruolo": r.role_label,
        }
        voti.append(
            {
                **shared,
                "ora": parse_time(r.time),
                "gol_squadra": counter(r.goals_for),
                "gol_avversario": counter(r.goals_against),
                "voto_fc": italian_decimal(r.voto_fc),
                "fantavoto_fc": italian_decimal(r.fantavoto_fc),
                "voto_stat": italian_decimal(r.voto_stat),
                "fantavoto_stat": italian_decimal(r.fantavoto_stat),
                "voto_italia": italian_decimal(r.voto_italia),
                "fantavoto_italia": italian_decimal(r.fantavoto_italia),
            }
        )
        bonus.append(
            {
                **shared,
                "ammonizione": int(r.ammonizione),
                "espulsione": int(r.espulsione),
                **{k: counter(r.bonus.get(k, "")) for k in BONUS_KEYS},
            }
        )
    return voti, bonus


def write_voti_csv(rows: list[PlayerMatchRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "stagione",
                "giornata",
                "data",
                "ora",
                "squadra",
                "avversario",
                "gol_squadra",
                "gol_avversario",
                "id",
                "nome",
                "ruolo_codice",
                "ruolo",
                "voto_fc",
                "fantavoto_fc",
                "voto_stat",
                "fantavoto_stat",
                "voto_italia",
                "fantavoto_italia",
            ]
        )
        for r in sorted(rows, key=lambda r: (r.season, r.giornata, r.team, r.name)):
            writer.writerow(
                [
                    r.season,
                    r.giornata,
                    r.date,
                    r.time,
                    r.team,
                    r.opponent,
                    r.goals_for,
                    r.goals_against,
                    r.player_id,
                    r.name,
                    r.role_code,
                    r.role_label,
                    r.voto_fc,
                    r.fantavoto_fc,
                    r.voto_stat,
                    r.fantavoto_stat,
                    r.voto_italia,
                    r.fantavoto_italia,
                ]
            )


def write_bonus_malus_csv(rows: list[PlayerMatchRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "stagione",
                "giornata",
                "data",
                "squadra",
                "avversario",
                "id",
                "nome",
                "ruolo_codice",
                "ruolo",
                "ammonizione",
                "espulsione",
                *BONUS_KEYS,
            ]
        )
        for r in sorted(rows, key=lambda r: (r.season, r.giornata, r.team, r.name)):
            writer.writerow(
                [
                    r.season,
                    r.giornata,
                    r.date,
                    r.team,
                    r.opponent,
                    r.player_id,
                    r.name,
                    r.role_code,
                    r.role_label,
                    int(r.ammonizione),
                    int(r.espulsione),
                    *[r.bonus.get(k, "") for k in BONUS_KEYS],
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
    args = parser.parse_args()

    total = 0
    for i, season in enumerate(args.seasons):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        total += fetch_season(season)

    if not total:
        print("No player rows found for any season — page structure may have changed.", file=sys.stderr)
        raise SystemExit(1)

    print(f"{total} player-match rows across {len(args.seasons)} seasons -> voti + bonus_malus")


if __name__ == "__main__":
    main()

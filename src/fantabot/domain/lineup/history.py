"""What the lineup projection may know about the past, and where "the past" ends. Pure.

The projection for matchday g sees only data from before g, in the backtest and live alike.
Three rules make "before g" mean what it says (SPEC A9):

* **The cutoff is by date, never by giornata.** A postponed match keeps its giornata:
  2025/26 giornata 16 had four matches played on 14-15 January 2026, after giornate 17-19
  had started. `giornata < 17` hands the model a result from the future. `before` cuts on
  `played_on < cutoff`, and `first_match_date` is the cutoff for giornata g — so a match
  on g's own opening day is g, not history.
* **`match_grain.squadra_raw` is the home team on both sides' rows.** Mandas (`LAZ`) reads
  Bologna, Lazio and Udinese over 2026/27's first three giornate: Bologna-Lazio,
  Lazio-Genoa, Udinese-Lazio. So a row says which *match* he played, and his *side* comes
  from his own club code (`quotazioni.squadra`) matched against either end: `side`,
  `plays_in`. A row whose club matches neither end is unresolvable and is dropped from the
  team effect and from `p_hist` by the caller (2.2% of rows).
* **Coach rows are not players.** They have no `player_id` and are excluded by the read.

`LineupHistory` is the port the projection and the backtest read through;
`adapters/persistence/repositories/lineup_history.py` implements it over Postgres.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from fantabot.domain.lineup.scoring import Appearance
from fantabot.domain.shared.club_names import code_for
from fantabot.domain.shared.values import SentimentRow

Side = Literal["home", "away"]


@dataclass(frozen=True, slots=True)
class Fixture:
    """One Serie A match, as `match_grain` records it."""

    season: str
    giornata: int
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int


@dataclass(frozen=True, slots=True)
class HistoryAppearance:
    """One player's row in one match: who, which match, and what he did in it."""

    player_id: int
    #: `ruolo_codice`, the Classic P/D/C/A letter.
    role: str
    fixture: Fixture
    scored: Appearance
    #: fantacalcio.it's own fantavoto, kept to check `scoring.lega_fantavoto` against.
    fantavoto_fc: float | None


@dataclass(frozen=True, slots=True)
class Valuation:
    """A player's preseason price and club for one season (`quotazioni`)."""

    #: The preseason `qi`: leak-free, unlike the end-of-season `fvm`/`qa` (SPEC A9).
    qi: int
    squadra: str


class LineupHistory(Protocol):
    """The reads the projection needs. Read-only; roles and valuations are separate reads
    because a replay takes roles from one season and `qi`/club from another (stats-1)."""

    def appearances(
        self, player_ids: Collection[int], *, seasons: Collection[str], before: date
    ) -> list[HistoryAppearance]: ...

    def roles(self, season: str) -> dict[int, tuple[str, ...]]: ...

    def valuations(self, season: str) -> dict[int, Valuation]: ...

    def fixtures(self, season: str) -> list[Fixture]: ...

    def calculated_scores(self, league_id: int) -> list[float]: ...

    def latest_sentiment(self, player_ids: Collection[int]) -> dict[int, SentimentRow]: ...


def first_match_date(fixtures: Iterable[Fixture], *, season: str, giornata: int) -> date:
    """The day giornata g opened: the cutoff for its history. Postponed matches of *earlier*
    giornate do not move it, and a postponed match of g itself is later than its first."""
    dates = [f.played_on for f in fixtures if f.season == season and f.giornata == giornata]
    if not dates:
        raise ValueError(f"no fixture of {season} giornata {giornata}")
    return min(dates)


def before(rows: Iterable[HistoryAppearance], cutoff: date) -> list[HistoryAppearance]:
    """The rows strictly before `cutoff`, in their given order."""
    return [row for row in rows if row.fixture.played_on < cutoff]


def side(fixture: Fixture, club: str) -> Side | None:
    """Which end of `fixture` the club with code `club` played, or `None` for neither."""
    if code_for(fixture.home) == club:
        return "home"
    if code_for(fixture.away) == club:
        return "away"
    return None


def plays_in(fixture: Fixture, club: str) -> bool:
    """Participation is home **or** away."""
    return side(fixture, club) is not None

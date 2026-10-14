"""Team strength: how many points a game a club is expected to take, known before a date. Pure.

The opponent adjustment (`matchup.py`) needs one number per club per day. It is a blend of what
the club's squad is worth and what the club has actually done, and **both halves are dated**:

* **The prior** is the club's squad value, the mean of its players' preseason `qi` over the
  season's own listone, z-scored across the season's clubs and mapped to points a game by a
  line fitted on *completed* seasons (`fit_value_map`). The mean beat the top-15 sum on the
  training seasons of the measurement. Past seasons' `fvm`/`qa` are end-of-season values and
  are not used here for the reason `history.py` gives: they would put the answer in the question.
* **The record** is the club's results in the season so far, **strictly before the cutoff**.
  `bisect_left` on the dates is what makes "strictly" hold for a match played on the cutoff
  day itself, which is the day's own result and not history.

They are blended `(k0 * prior + points) / (k0 + matches)`. `PRIOR_MATCHES` is that k0.

**No clock and no I/O**: `before` is a parameter, so a test can scramble every result dated on
or after it and watch the estimate not move, and a replay of a past day computes what the
hourly job would have computed on it.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from fantabot.domain.lineup.history import Fixture, Valuation
from fantabot.domain.shared.club_names import code_for

#: k0, in **matches**: how much of a club's squad-value prior survives its results. 64 is
#: nearly two seasons' worth, so the estimate is mostly the prior and the results move it
#: gradually: with k0 = 64 the record carries about a quarter of the weight at midseason (19 of 83).
#:
#: Declared, not fitted at run time. Provenance: the measurement of 2026-10-14 chose k0 per
#: quantity by leave-one-season-out squared error of the blend as a predictor of each club's
#: *future* matches, on training seasons only; it was 48 on the 2022/23-2023/24 fold and 64
#: on the folds that include 2024/25, with the loss flat between 32 and 128. A run-time fit
#: would be a second model to maintain for a number whose neighbourhood costs nothing.
PRIOR_MATCHES = 64.0

_WIN, _DRAW = 3.0, 1.0


@dataclass(frozen=True, slots=True)
class ValueMap:
    """The line from a club's z-scored squad value to its points a game."""

    intercept: float
    slope: float


@dataclass(frozen=True, slots=True)
class SeasonTable:
    """One season's clubs: their squad values and their dated results so far."""

    season: str
    #: Mean preseason `qi` per club, z-scored across the season's clubs (population sd).
    z: Mapping[str, float]
    #: Per club, the dates of its matches, ascending.
    dates: Mapping[str, tuple[date, ...]]
    #: Per club, `running[c][k]` = its points after its first `k` matches (`running[c][0]` = 0).
    running: Mapping[str, tuple[float, ...]]

    @property
    def complete(self) -> bool:
        """Whether every club has played every other twice, a final per-game record.

        Derived from the number of clubs rather than from a literal 38, so the same rule
        covers a league of another size and the four-club leagues the tests build.
        """
        full = 2 * (len(self.z) - 1)
        return full > 0 and all(len(self.dates.get(club, ())) == full for club in self.z)

    def final_ppg(self, club: str) -> float:
        """The club's points per match over every match the table holds.

        Raises:
            ZeroDivisionError: the club has no matches; check `complete` first.
        """
        return self.running[club][-1] / len(self.dates[club])


def season_table(
    season: str, valuations: Mapping[int, Valuation], fixtures: Sequence[Fixture]
) -> SeasonTable | None:
    """The season's table, or `None` when its squad values cannot be z-scored.

    The clubs are those of the season's valuations. A club the listone does not hold gets no
    entry of its own (it has no squad value to blend) while its listed opponent still counts
    the match, so the season no longer has the double round robin `complete` asks for and
    stops training the value map: one season lost, never the whole adjustment. A club with
    no fixture yet — the whole league before its first round — still has a prior. A match
    listed twice, which a `DISTINCT` over goals can produce when two player rows disagree,
    counts once.

    A single club is not refused here: its z-score has no spread, and the `sd` guard below
    is the one place that says so.
    """
    by_club: dict[str, list[int]] = defaultdict(list)
    for valuation in valuations.values():
        by_club[valuation.squadra].append(valuation.qi)
    if not by_club:
        return None
    raw = {club: sum(q) / len(q) for club, q in by_club.items()}
    centre = sum(raw.values()) / len(raw)
    sd = math.sqrt(sum((v - centre) ** 2 for v in raw.values()) / len(raw))
    if sd == 0.0:
        return None

    played: dict[str, list[tuple[date, float]]] = {club: [] for club in raw}
    seen: set[tuple[int, str, str]] = set()
    for f in fixtures:
        home, away = code_for(f.home), code_for(f.away)
        if (f.giornata, home, away) in seen:
            continue
        seen.add((f.giornata, home, away))
        points = (
            (_WIN, 0.0) if f.home_goals > f.away_goals
            else (0.0, _WIN) if f.home_goals < f.away_goals
            else (_DRAW, _DRAW)
        )
        for club, earned in ((home, points[0]), (away, points[1])):
            if club in played:
                played[club].append((f.played_on, earned))

    dates: dict[str, tuple[date, ...]] = {}
    running: dict[str, tuple[float, ...]] = {}
    for club, matches in played.items():
        # `expected_ppg` bisects these dates, which must ascend. A postponed match keeps its
        # giornata but is dated after later rounds, so the order the rows arrive in is not it.
        matches.sort(key=lambda m: m[0])
        dates[club] = tuple(d for d, _ in matches)
        total, cumulative = 0.0, [0.0]
        for _, earned in matches:
            total += earned
            cumulative.append(total)
        running[club] = tuple(cumulative)
    return SeasonTable(
        season=season,
        z={club: (value - centre) / sd for club, value in raw.items()},
        dates=dates,
        running=running,
    )


def fit_value_map(tables: Collection[SeasonTable]) -> ValueMap | None:
    """The least-squares line of final points a game on z, pooled over the complete tables.

    `None` when no table is complete, when z has no spread, and when the slope is not
    positive: a map that says a stronger squad takes no more points would turn every estimate
    on its head, and it can only come from a season the data got wrong.

    The sums are plain and taken in the order the tables are given, so the last bit of the
    line depends on that order. A caller that must be bit-reproducible passes the tables in
    a canonical one (`matchup.adjust` sorts by season).
    """
    xs: list[float] = []
    ys: list[float] = []
    for table in tables:
        if not table.complete:
            continue
        for club, z in table.z.items():
            xs.append(z)
            ys.append(table.final_ppg(club))
    if len(xs) < 2:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    if not slope > 0.0:
        return None
    return ValueMap(intercept=mean_y - slope * mean_x, slope=slope)


def expected_ppg(
    table: SeasonTable,
    value_map: ValueMap,
    club: str,
    *,
    before: date,
    prior_matches: float = PRIOR_MATCHES,
) -> float | None:
    """The club's blended points a game from results dated strictly before `before`.

    `None` for a club the season has no squad value for.
    """
    z = table.z.get(club)
    if z is None:
        return None
    matches = bisect_left(table.dates[club], before)
    prior = value_map.intercept + value_map.slope * z
    return (prior_matches * prior + table.running[club][matches]) / (prior_matches + matches)

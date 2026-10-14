"""A four-club league small enough to check by hand, shared by the opponent-adjustment tests.

Four clubs play a double round robin: six giornate of two matches each, 12 fixtures a season
and 6 matches a club. That is `2 * (4 - 1)` per club, which is the property "a complete season"
means to `domain/lineup/strength` — it is derived from the number of clubs, so these small
leagues are complete without being 380 rows long.

The second half of this module is a **plain-loop re-derivation** of the strength feature,
written without importing anything from `fantabot.domain.lineup.strength`. A test that planted
a known effect with the module's own arithmetic would pass for any arithmetic; planting it with
this one means a wrong feature (a map that includes the row's own season, a cutoff that admits
the day's own results) recovers the wrong slope.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta

from fantabot.domain.lineup.history import Fixture, Valuation
from fantabot.domain.shared.club_names import code_for

NAMES = ("Alpha", "Bravo", "Charlie", "Delta")
#: `ALP BRA CHA DEL`, from the names the way `match_grain` and `quotazioni` relate.
CODES = tuple(code_for(name) for name in NAMES)
#: The first leg's three rounds as (home, away) club indexes; the second leg swaps the ends.
LEG = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))

Result = Callable[[int, int, int], tuple[int, int]]


def start_of(season: str) -> date:
    """The first of September of the season's opening year."""
    return date(int(season[:4]), 9, 1)


def shifted_result(shift: int) -> Result:
    """Goals from (giornata, home index, away index): varied, deterministic and season-specific.

    The higher-indexed club, which has the higher squad value, gets a goal on top, so that
    squad value predicts points the way it does in a real league and a value map fitted on
    any of these seasons has a positive slope.
    """

    def _goals(giornata: int, home: int, away: int) -> tuple[int, int]:
        return (
            (giornata + home + shift) % 3 + int(home > away),
            (2 * giornata + away + shift) % 3 + int(away > home),
        )

    return _goals


def fixtures(
    season: str, *, result: Result | None = None, shift: int = 0, rounds: int = 6
) -> list[Fixture]:
    """`rounds` giornate of the double round robin. The two matches of a round are a day apart,
    so "the day a giornata opened" and "the day a match was played" are different dates."""
    goals = result or shifted_result(shift)
    out: list[Fixture] = []
    for giornata in range(1, rounds + 1):
        swapped = ((giornata - 1) // 3) % 2 == 1
        for k, (home, away) in enumerate(LEG[(giornata - 1) % 3]):
            if swapped:
                home, away = away, home
            home_goals, away_goals = goals(giornata, home, away)
            out.append(
                Fixture(
                    season=season, giornata=giornata,
                    played_on=start_of(season) + timedelta(days=7 * (giornata - 1) + k),
                    home=NAMES[home], away=NAMES[away],
                    home_goals=home_goals, away_goals=away_goals,
                )
            )
    return out


def player_id(club: int, k: int) -> int:
    """The id of club `club`'s k-th player, stable across seasons."""
    return 10 * club + k + 1


def valuations(*, per_club: int = 2) -> dict[int, Valuation]:
    """`per_club` players a club (an even number), club means 10, 20, 30 and 40."""
    return {
        player_id(c, k): Valuation(qi=10 + 10 * c + (1 if k % 2 == 0 else -1), squadra=code)
        for c, code in enumerate(CODES)
        for k in range(per_club)
    }


# --------------------------------------------------------------- plain-loop re-derivation


def plain_squad_z(vals: Mapping[int, Valuation]) -> dict[str, float]:
    """Mean qi per club, z-scored with the population standard deviation."""
    by: dict[str, list[float]] = {}
    for v in vals.values():
        by.setdefault(v.squadra, []).append(float(v.qi))
    raw = {club: sum(q) / len(q) for club, q in by.items()}
    mean = sum(raw.values()) / len(raw)
    sd = (sum((x - mean) ** 2 for x in raw.values()) / len(raw)) ** 0.5
    return {club: (x - mean) / sd for club, x in raw.items()}


def plain_points(fx: Sequence[Fixture]) -> dict[str, list[tuple[date, float]]]:
    """Each club's (date, points) per match: three for a win, one for a draw."""
    out: dict[str, list[tuple[date, float]]] = {}
    for f in fx:
        home, away = code_for(f.home), code_for(f.away)
        hp = 3.0 if f.home_goals > f.away_goals else 1.0 if f.home_goals == f.away_goals else 0.0
        ap = 3.0 if f.away_goals > f.home_goals else 1.0 if f.home_goals == f.away_goals else 0.0
        out.setdefault(home, []).append((f.played_on, hp))
        out.setdefault(away, []).append((f.played_on, ap))
    return out


def plain_map(
    vals: Mapping[str, Mapping[int, Valuation]],
    fx: Mapping[str, Sequence[Fixture]],
    seasons: Sequence[str],
) -> tuple[float, float]:
    """(intercept, slope) of each club's final points per game on its z, pooled over `seasons`."""
    xs: list[float] = []
    ys: list[float] = []
    for s in seasons:
        z, pts = plain_squad_z(vals[s]), plain_points(fx[s])
        for club in z:
            xs.append(z[club])
            ys.append(sum(p for _, p in pts[club]) / len(pts[club]))
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    slope = (n * sum(a * b for a, b in zip(xs, ys, strict=True)) - sx * sy) / (
        n * sum(a * a for a in xs) - sx * sx
    )
    return (sy - slope * sx) / n, slope


def plain_expected(
    vals: Mapping[str, Mapping[int, Valuation]],
    fx: Mapping[str, Sequence[Fixture]],
    *,
    map_seasons: Sequence[str],
    season: str,
    club: str,
    before: date,
    prior_matches: float = 64.0,
) -> float:
    """The club's blended points per game in `season`, from results dated strictly before
    `before`, with the prior line fitted on `map_seasons`."""
    icpt, slope = plain_map(vals, fx, map_seasons)
    prior = icpt + slope * plain_squad_z(vals[season])[club]
    played = [p for d, p in plain_points(fx[season]).get(club, []) if d < before]
    return (prior_matches * prior + sum(played)) / (prior_matches + len(played))


def opens(fx: Sequence[Fixture], giornata: int) -> date:
    """The day giornata g opened."""
    return min(f.played_on for f in fx if f.giornata == giornata)

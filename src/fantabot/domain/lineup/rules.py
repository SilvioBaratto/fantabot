"""A lega's scoring rules that shape the lineup, from `settings/calculate`. Pure.

Three parts of that body matter here (read live 2026-09-23 on the operator's three leghe):

* `smodd` — the *modificatore difesa*. `smodld`/`smodlu` bound the average vote, `smodva` is
  the bonus table, `smoddg` whether the keeper's vote is in the average. Null when the lega
  does not play it.
* `smodcp` — a captain modifier (3677376 only): the captain's own vote, banded the same way,
  gives a bonus or malus. Parsed and shown; nothing decides on it yet.
* `subst` — `ssnum` is the cap on substitutions per matchday (5 everywhere measured);
  `sstype` is shown and not interpreted, because its meaning is unmeasured.

**The band reading is inferred from the table's length, and checked.** Two layouts occur:

* *Bands* (every `smodd` measured): `len(smodva) == (lu - ld) / 0.25 + 2`. Index 0 is below
  `ld`, index i >= 1 the 0.25-wide band from `ld + (i - 1) * 0.25`, the last `lu` and up.
* *Points* (`smodcp` on 3677376: 4.5 -> 7.5, seven values): one value per step of
  `(lu - ld) / (len - 1)` — 0.5 there — the first for `ld` and below, the last for `lu` and up;
  the familiar captain table (-1.5 at 4.5 … +1.5 at 7.5).

Both are stored the same way, as the vote at which each value after the first starts. Only the
layout each modifier was measured in is accepted for it; a table that fits no accepted layout
is not guessed at: it parses to None with a reason, and the caller falls
back to the pre-rules behaviour.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Width of one vote band in the modifier tables.
BAND_STEP = 0.25


@dataclass(frozen=True)
class BandedModifier:
    """A bonus looked up from a vote: `values[0]` below `edges[0]`, `values[i + 1]` from
    `edges[i]` up to the next edge."""

    lower: float
    upper: float
    values: tuple[float, ...]
    edges: tuple[float, ...]

    def bonus_for(self, avg: float) -> float:
        """The table's value for this vote."""
        return self.values[bisect.bisect_right(self.edges, avg + 1e-9)]

    def band_edges(self) -> list[float]:
        """The vote at which each value after the first starts."""
        return list(self.edges)

    def expected(self, mean: float, sd: float) -> float:
        """E[value] when the vote is Normal(`mean`, `sd`) — the table integrated over it."""
        if sd <= 0:
            return self.bonus_for(mean)

        def cdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))

        total = self.values[0] * cdf(self.edges[0])
        for i, edge in enumerate(self.edges):
            upper = cdf(self.edges[i + 1]) if i + 1 < len(self.edges) else 1.0
            total += self.values[i + 1] * (upper - cdf(edge))
        return total


@dataclass(frozen=True)
class DefenceModifier(BandedModifier):
    """`smodd`. `includes_keeper` is `smoddg`: the keeper's vote is in the average."""

    includes_keeper: bool = True


@dataclass(frozen=True)
class SubstitutionRules:
    """`subst`: the per-matchday cap and the (uninterpreted) substitution type."""

    max_subs: int
    kind: int | None


@dataclass(frozen=True)
class LeagueRules:
    """What `settings/calculate` says about lineups. A None part is absent or unreadable;
    `problems` says which, and why, so the CLI can show it instead of guessing."""

    defence: DefenceModifier | None
    captain: BandedModifier | None
    subs: SubstitutionRules | None
    problems: tuple[str, ...] = ()


def _banded(
    raw: Mapping[str, Any], name: str, *, allow_points: bool
) -> tuple[BandedModifier | None, str | None]:
    try:
        lower = float(raw["smodld"])
        upper = float(raw["smodlu"])
        values = tuple(float(v) for v in raw["smodva"])
    except (KeyError, TypeError, ValueError):
        return None, f"{name}: missing or non-numeric smodld/smodlu/smodva"
    n = len(values)
    if upper <= lower or n < 2:
        return None, f"{name}: empty or inverted range {lower}-{upper} — table not read"
    if n == round((upper - lower) / BAND_STEP) + 2:
        edges = tuple(lower + i * BAND_STEP for i in range(n - 1))
    elif not allow_points:
        return None, (
            f"{name}: {n} values for {lower}-{upper}, expected "
            f"{round((upper - lower) / BAND_STEP) + 2} at {BAND_STEP} per band — table not read"
        )
    else:
        step = (upper - lower) / (n - 1)
        if abs(step / BAND_STEP - round(step / BAND_STEP)) > 1e-9:
            return None, (
                f"{name}: {n} values for {lower}-{upper} fit neither {BAND_STEP}-wide bands "
                f"nor evenly stepped points — table not read"
            )
        edges = tuple(lower + (i + 1) * step for i in range(n - 1))
    return BandedModifier(lower=lower, upper=upper, values=values, edges=edges), None


def rules_from_calculate(body: Mapping[str, Any]) -> LeagueRules:
    """Parse a `settings/calculate` body. Never raises: an unreadable part is None and
    named in `problems`."""
    problems: list[str] = []

    defence: DefenceModifier | None = None
    smodd = body.get("smodd")
    if isinstance(smodd, Mapping):
        banded, problem = _banded(smodd, "smodd", allow_points=False)
        if banded is not None:
            defence = DefenceModifier(
                lower=banded.lower,
                upper=banded.upper,
                values=banded.values,
                edges=banded.edges,
                includes_keeper=bool(smodd.get("smoddg", True)),
            )
        if problem:
            problems.append(problem)

    captain: BandedModifier | None = None
    smodcp = body.get("smodcp")
    if isinstance(smodcp, Mapping):
        captain, problem = _banded(smodcp, "smodcp", allow_points=True)
        if problem:
            problems.append(problem)

    subs: SubstitutionRules | None = None
    subst = body.get("subst")
    if isinstance(subst, Mapping):
        try:
            kind = subst.get("sstype")
            subs = SubstitutionRules(
                max_subs=int(subst["ssnum"]), kind=int(kind) if kind is not None else None
            )
        except (KeyError, TypeError, ValueError):
            problems.append("subst: missing or non-numeric ssnum")

    return LeagueRules(defence=defence, captain=captain, subs=subs, problems=tuple(problems))


def describe_bands(modifier: BandedModifier) -> Sequence[str]:
    """`["<6.00: 0", "6.00+: 1", "6.25+: 2", …]` — the table as the operator reads it."""
    edges = modifier.band_edges()
    labels = [f"<{edges[0]:.2f}: {modifier.values[0]:g}"]
    labels += [f"{edge:.2f}+: {v:g}" for edge, v in zip(edges, modifier.values[1:], strict=True)]
    return labels

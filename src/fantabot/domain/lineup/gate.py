"""Gate 1's statistic: the two-way cluster bootstrap over a replay. Pure, seeded.

Split from `backtest.py` so the **replay** stays free of numpy. `lineup shadow-report`
recomputes a submitted lineup with `backtest.field`, and a grading command that loaded
scipy for an interval it never computes would put both libraries on an import path the
hourly job's own surface can reach (AD3). The gate imports this; nothing else does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from fantabot.domain.lineup.backtest import Paired


@dataclass(frozen=True, slots=True)
class Interval:
    """A bootstrap estimate: the point, the interval, and what it was resampled over."""

    mean: float
    low: float
    high: float
    #: Resamples drawn, and the two cluster counts they were drawn over.
    draws: int
    rooms: int
    giornate: int

    @property
    def positive(self) -> bool:
        """Whether the whole interval is above zero — Gate 1's own question."""
        return self.low > 0.0


def two_way_bootstrap(
    paired: Sequence[Paired],
    *,
    rng: np.random.Generator,
    value: str = "delta_points",
    draws: int = 2_000,
    alpha: float = 0.05,
) -> Interval:
    """The two-way cluster bootstrap over rooms **and** giornate (Decision 13).

    The observations are not independent in two directions at once and both are obvious once
    named. Within a **room** the rosters play each other, so all-play-all makes one roster's
    good week another's bad one — the deltas inside a room sum to something near zero by
    construction. Within a **giornata** every room shares the same Serie A results, so a week
    when the favourites all blanked moves every room together. Resampling rows would treat
    4,884 of them as 4,884 independent facts when they are closer to 17 x 33.

    So both cluster labels are resampled with replacement and the cell `(room, giornata)` is
    taken when **both** of its labels were drawn — Cameron-Gelbach-Miller's two-way pairs
    bootstrap, in its simplest form. A resample that happens to select no cell at all is
    skipped rather than counted as a zero: zero is a value the statistic can legitimately
    take, and folding an empty draw in as one would pull every interval toward it.

    `rng` is the caller's seeded `Generator` (AD6). Nothing here reads a clock or a global.
    """
    if not paired:
        raise ValueError("a bootstrap needs at least one paired observation")
    generator = rng
    rooms = sorted({row.room for row in paired})
    giornate = sorted({row.giornata for row in paired})
    room_index = {name: i for i, name in enumerate(rooms)}
    giornata_index = {g: i for i, g in enumerate(giornate)}

    values = np.array([float(getattr(row, value)) for row in paired], dtype=np.float64)
    row_room = np.array([room_index[row.room] for row in paired], dtype=np.intp)
    row_giornata = np.array([giornata_index[row.giornata] for row in paired], dtype=np.intp)

    means: list[float] = []
    for _ in range(draws):
        # `bincount` turns "room r was drawn twice" into a weight of 2, which is what a
        # pairs bootstrap means: a cluster drawn twice contributes twice.
        room_weight = np.bincount(
            generator.integers(0, len(rooms), len(rooms)), minlength=len(rooms)
        )
        giornata_weight = np.bincount(
            generator.integers(0, len(giornate), len(giornate)), minlength=len(giornate)
        )
        weight = room_weight[row_room] * giornata_weight[row_giornata]
        total = weight.sum()
        if total == 0:
            continue
        means.append(float((values * weight).sum() / total))
    if not means:  # pragma: no cover - needs every resample to select nothing
        raise ValueError("every resample selected no observation")
    sample = np.sort(np.array(means, dtype=np.float64))
    return Interval(
        mean=float(values.mean()),
        low=float(np.quantile(sample, alpha / 2.0)),
        high=float(np.quantile(sample, 1.0 - alpha / 2.0)),
        draws=len(sample),
        rooms=len(rooms),
        giornate=len(giornate),
    )

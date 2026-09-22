"""What the opponent scores this matchday: a variance-preserving KDE over the lega's own
calculated fantapunti, integrated analytically over the goal ladder. Pure.

The sample is every calculated `points_home`/`points_away` of the lega's current competition
(24 on 2026-09-22). It is small, so it is smoothed rather than used raw — but a Gaussian KDE
is a mixture of normals *around* the data, and its variance is the sample's **plus** the
bandwidth squared. Left alone that hands every opponent a wider spread than they have, which
in this objective is a systematic error and not a rounding one: a wider opponent makes an
unlikely win look likelier, and the whole plan is chosen on `3·P(W) + P(D)`.

So the centres are shrunk toward the mean by `1/sqrt(1 + f²)` before smoothing, with `f`
Scott's factor. The mixture's variance is then exactly the sample's (stats-11), and its mean
is unchanged.

**The goals are integrated, never drawn** (SPEC A17(5)): `P(goals = k)` is the KDE's own CDF
differenced across the ladder's bands, a sum of normal CDFs. The opponent therefore adds no
Monte Carlo noise at all, which is what lets the evaluator's budget go to the draws that do
vary — and it is exact, so two runs of the same plan cannot disagree about the opponent.

Fewer than eight calculated rounds, or a sample with no spread at all, raises
`OpponentUnavailable` rather than inventing a distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import numpy.typing as npt
from scipy.special import ndtr

from fantabot.domain.lineup.errors import OpponentUnavailable

Floats = npt.NDArray[np.float64]

#: Calculated rounds below which the projection path refuses (SPEC "Opponent").
MIN_SCORES = 8


@dataclass(frozen=True, slots=True)
class Opponent:
    """The fitted distribution: equal-weight normals at `centres`, each of width `bandwidth`."""

    centres: Floats
    bandwidth: float

    @property
    def mean(self) -> float:
        return float(self.centres.mean())

    @property
    def variance(self) -> float:
        """The mixture's own variance: the centres' spread plus the bandwidth's."""
        return float(self.centres.var() + self.bandwidth**2)

    def cdf(self, points: float) -> float:
        """`P(score <= points)`, the mean of the centres' normal CDFs."""
        return float(np.mean(ndtr((points - self.centres) / self.bandwidth)))


def fit(scores: Sequence[float], *, minimum: int = MIN_SCORES) -> Opponent:
    """Smooth `scores` into an opponent whose variance is theirs, not theirs inflated."""
    if len(scores) < minimum:
        raise OpponentUnavailable(len(scores), minimum)
    sample = np.asarray(scores, dtype=np.float64)
    spread = float(sample.std())
    if spread <= 0:
        raise OpponentUnavailable(
            len(scores), minimum, reason="every calculated round read the same total"
        )
    # Scott's rule for one dimension, which is what `scipy.stats.gaussian_kde` uses, and the
    # shrink that makes `centres.var() + bandwidth**2` come back to the sample's variance.
    factor = len(sample) ** (-0.2)
    shrink = 1.0 / np.sqrt(1.0 + factor**2)
    mean = float(sample.mean())
    centres = mean + (sample - mean) * shrink
    return Opponent(centres=centres, bandwidth=factor * spread * float(shrink))


def goal_probabilities(
    opponent: Opponent, *, threshold: float, steps: Sequence[float]
) -> tuple[float, ...]:
    """`P(opponent goals = k)` for k = 0 up the ladder, one entry per rung plus the blank.

    The bands are `scoring.goals`' own: below `threshold` is 0, and each `step` is a further
    goal at `threshold + step`. Differencing the CDF across them is the integral of the
    density over each, exactly.
    """
    edges = [threshold, *(threshold + step for step in steps)]
    below = [opponent.cdf(edge) for edge in edges]
    return (
        below[0],
        *(high - low for low, high in pairwise(below)),
        1.0 - below[-1],
    )

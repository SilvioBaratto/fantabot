"""The opponent's score distribution: a variance-preserving KDE, integrated analytically.

The lega's own calculated fantapunti are the sample. A plain Gaussian KDE is a mixture of
normals *around* the data, so its variance is the sample's plus the bandwidth's — smoothing
inflates the spread, which in this model would hand every opponent a longer tail than they
have. Shrinking the centres toward the mean first puts it back exactly (stats-11).

`P(opponent goals = k)` is then the KDE's own CDF differenced over the ladder's bands, so the
opponent term carries no Monte Carlo noise at all (SPEC A17(5)).
"""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.stats import gaussian_kde

from fantabot.domain.lineup.errors import LineupError, OpponentUnavailable
from fantabot.domain.lineup.opponent import MIN_SCORES, fit, goal_probabilities

#: 24 calculated fantapunti, the shape of this lega's own: mid-60s, a long-ish right tail.
SCORES = [
    58.5, 61.0, 62.5, 63.0, 64.5, 65.0, 66.0, 66.5, 67.0, 67.5, 68.0, 68.5,
    69.0, 70.0, 70.5, 71.5, 72.0, 73.0, 74.5, 76.0, 77.5, 79.0, 82.0, 88.5,
]
#: This lega's ladder: 66 for the first goal, then every 6 points (`stlmt`/`stgoal`).
THRESHOLD = 66.0
STEPS = (6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0)


class TestTheFit:
    def test_it_preserves_the_sample_s_variance(self) -> None:
        """stats-11: the KDE's variance is the sample's, not the sample's plus h²."""
        opponent = fit(SCORES)

        assert opponent.variance == pytest.approx(float(np.var(SCORES)), rel=0.05)

    def test_a_plain_kde_would_have_inflated_it(self) -> None:
        """The defect this guards: scipy's own KDE over the raw sample is visibly wider."""
        plain = gaussian_kde(SCORES)
        inflated = float(np.var(SCORES)) * (1 + plain.factor**2)

        assert inflated > float(np.var(SCORES)) * 1.02
        assert fit(SCORES).variance < inflated

    def test_it_keeps_the_sample_s_mean(self) -> None:
        assert fit(SCORES).mean == pytest.approx(float(np.mean(SCORES)))

    def test_the_bandwidth_is_scott_s_over_the_shrunk_centres(self) -> None:
        """Scipy's rule, applied to the centres this actually smooths."""
        opponent = fit(SCORES)
        scott = gaussian_kde(opponent.centres).factor

        assert opponent.bandwidth == pytest.approx(scott * float(np.std(opponent.centres)))

    def test_too_few_scores_refuse(self) -> None:
        with pytest.raises(OpponentUnavailable):
            fit(SCORES[: MIN_SCORES - 1])

    def test_the_refusal_is_one_of_the_lineup_family(self) -> None:
        """Both surfaces catch `LineupError` and print it; a refusal outside that family
        would leave the command as an unhandled traceback instead."""
        with pytest.raises(LineupError):
            fit(SCORES[: MIN_SCORES - 1])

    def test_exactly_the_floor_is_enough(self) -> None:
        assert fit(SCORES[:MIN_SCORES]).variance > 0

    def test_the_floor_is_eight(self) -> None:
        assert MIN_SCORES == 8

    def test_a_sample_with_no_spread_refuses(self) -> None:
        """Every round the same total is not a distribution to draw an opponent from, and a
        zero bandwidth would divide by 0 in the CDF."""
        with pytest.raises(OpponentUnavailable):
            fit([70.0] * 24)


class TestTheCdf:
    def test_it_runs_from_0_to_1(self) -> None:
        opponent = fit(SCORES)

        assert opponent.cdf(-1e6) == pytest.approx(0.0, abs=1e-12)
        assert opponent.cdf(1e6) == pytest.approx(1.0, abs=1e-12)

    def test_it_never_decreases(self) -> None:
        opponent = fit(SCORES)
        values = [opponent.cdf(x) for x in range(40, 120, 2)]

        assert values == sorted(values)

    def test_it_is_the_mean_of_the_centres_own_normals(self) -> None:
        opponent = fit(SCORES)
        point = 70.0
        by_hand = float(
            np.mean(
                [
                    0.5 * (1 + math.erf((point - centre) / (opponent.bandwidth * math.sqrt(2))))
                    for centre in opponent.centres
                ]
            )
        )

        assert opponent.cdf(point) == pytest.approx(by_hand, abs=1e-12)


class TestTheGoalProbabilities:
    def test_there_is_one_for_every_rung_and_they_sum_to_one(self) -> None:
        probabilities = goal_probabilities(fit(SCORES), threshold=THRESHOLD, steps=STEPS)

        assert len(probabilities) == len(STEPS) + 2  # 0 goals, then one per rung
        assert sum(probabilities) == pytest.approx(1.0, abs=1e-12)
        assert all(p >= 0 for p in probabilities)

    def test_each_band_matches_a_brute_force_integral(self) -> None:
        """The analytic claim: differencing the CDF is integrating the density."""
        opponent = fit(SCORES)
        probabilities = goal_probabilities(opponent, threshold=THRESHOLD, steps=STEPS)

        def density(x: float) -> float:
            return float(
                np.mean(
                    np.exp(-0.5 * ((x - opponent.centres) / opponent.bandwidth) ** 2)
                    / (opponent.bandwidth * math.sqrt(2 * math.pi))
                )
            )

        edges = [THRESHOLD, *(THRESHOLD + step for step in STEPS)]
        bounds = [(-np.inf, edges[0]), *pairwise(edges), (edges[-1], np.inf)]

        for expected, (low, high) in zip(probabilities, bounds, strict=True):
            integral, _ = quad(density, low, high, limit=200)
            assert integral == pytest.approx(expected, abs=1e-9)

    def test_a_lega_that_scores_below_the_first_rung_mostly_draws_a_blank(self) -> None:
        low = [x - 20.0 for x in SCORES]

        probabilities = goal_probabilities(fit(low), threshold=THRESHOLD, steps=STEPS)

        assert probabilities[0] > 0.9

    def test_a_ladder_with_no_rungs_is_the_threshold_alone(self) -> None:
        opponent = fit(SCORES)

        probabilities = goal_probabilities(opponent, threshold=THRESHOLD, steps=())

        assert probabilities == pytest.approx(
            (opponent.cdf(THRESHOLD), 1 - opponent.cdf(THRESHOLD))
        )

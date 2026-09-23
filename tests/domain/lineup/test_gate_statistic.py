"""Gate 1's statistic: the two-way cluster bootstrap. Pure, seeded, and checked analytically.

Split from `test_backtest.py` because `domain/lineup/gate.py` is split from
`backtest.py` — the replay must stay free of numpy so `lineup shadow-report` can recompute
a submitted lineup without loading scipy for an interval it never computes (AD3).

The interval is never pinned to a number somebody once got. It is checked against cases
whose answer is known: a constant difference has to give a point interval, noise has to
widen it around the same mean, and the same seed has to give the same answer twice.
"""

from __future__ import annotations

import numpy as np
import pytest

from fantabot.domain.lineup.backtest import Paired
from fantabot.domain.lineup.gate import two_way_bootstrap


def _rows(delta: float, *, rooms: int = 8, giornate: int = 20, spread: float = 0.0) -> list[Paired]:
    out: list[Paired] = []
    for r in range(rooms):
        for g in range(giornate):
            shift = spread * ((r + g) % 3 - 1)
            out.append(
                Paired(
                    room=f"room{r}", buyer="b0", giornata=g,
                    baseline_points=1.0, model_points=1.0 + delta + shift,
                    baseline_fantapunti=60.0, model_fantapunti=60.0 + delta + shift,
                )
            )
    return out


class TestTheBootstrap:
    def test_a_constant_difference_has_a_degenerate_interval(self) -> None:
        """Every cluster carries the same delta, so every resample is that delta: the answer
        is known exactly, and an interval that is not a point is a resampler that is mixing
        in something the data does not have."""
        interval = two_way_bootstrap(_rows(0.5), rng=np.random.default_rng(3), draws=200)

        assert interval.mean == pytest.approx(0.5)
        assert (interval.low, interval.high) == pytest.approx((0.5, 0.5))
        assert interval.positive

    def test_a_zero_difference_is_not_positive(self) -> None:
        interval = two_way_bootstrap(_rows(0.0), rng=np.random.default_rng(3), draws=200)

        assert interval.positive is False

    def test_a_negative_difference_is_not_positive(self) -> None:
        interval = two_way_bootstrap(_rows(-0.5), rng=np.random.default_rng(3), draws=200)

        assert interval.positive is False

    def test_noise_widens_the_interval_around_the_same_mean(self) -> None:
        tight = two_way_bootstrap(_rows(0.5), rng=np.random.default_rng(3), draws=400)
        loose = two_way_bootstrap(
            _rows(0.5, spread=0.4), rng=np.random.default_rng(3), draws=400
        )

        assert loose.mean == pytest.approx(tight.mean, abs=1e-9)
        assert (loose.high - loose.low) > (tight.high - tight.low)

    def test_the_same_seed_gives_the_same_interval(self) -> None:
        rows = _rows(0.3, spread=0.5)

        one = two_way_bootstrap(rows, rng=np.random.default_rng(11), draws=300)
        two = two_way_bootstrap(rows, rng=np.random.default_rng(11), draws=300)

        assert one == two

    def test_both_cluster_counts_are_reported(self) -> None:
        interval = two_way_bootstrap(
            _rows(0.1, rooms=5, giornate=9), rng=np.random.default_rng(1), draws=100
        )

        assert (interval.rooms, interval.giornate) == (5, 9)

    def test_it_can_be_asked_about_fantapunti(self) -> None:
        """The guard is a different statistic over the same rows, not a second bootstrap."""
        interval = two_way_bootstrap(
            _rows(-0.5), rng=np.random.default_rng(3), draws=200, value="delta_fantapunti"
        )

        assert interval.mean == pytest.approx(-0.5)

    def test_an_empty_corpus_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            two_way_bootstrap([], rng=np.random.default_rng(1))

    def test_one_room_of_one_giornata_is_still_answerable(self) -> None:
        """Degenerate, and the right answer is a point interval rather than a crash: the
        sweep season can legitimately be asked about a single room."""
        interval = two_way_bootstrap(
            _rows(0.2, rooms=1, giornate=1), rng=np.random.default_rng(1), draws=50
        )

        assert interval.mean == pytest.approx(0.2)
        assert (interval.rooms, interval.giornate) == (1, 1)

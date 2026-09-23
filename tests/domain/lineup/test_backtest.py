"""The replay and the gate's statistic. Pure — no database, no clock, no network.

Gate 1 grades two arms on the same rosters, the same giornate and the same absences, so
everything that is not the plan has to be identical. What is pinned here:

* **the realized score is the platform's arithmetic** — the *fielded* eleven, one point off
  per out-of-position man, and the lega's own ladder. A replay that scored the submitted
  eleven would grade a game nobody played, and it is the malus and the man short that make
  the two different;
* **all-play-all, reported per fixture**, so the week's luck cancels and a 10-team room does
  not outweigh an 8-team one in the pooled corpus. The league points of a hand-built room
  are hand-computed;
* **the baseline is the spreadsheet a manager would keep** — a five-vote mean and a binary
  `p` — because a baseline with the model's machinery in it grades the machinery against
  itself;
* **the bootstrap clusters both ways**, and its interval is checked against a case whose
  answer is known rather than against a number somebody once got.

The board is 343 again, with roles chosen so who comes on is readable by hand.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pytest
from test_simulate import RULES

from fantabot.domain.lineup.backtest import (
    BASELINE_WINDOW,
    BURN_IN,
    Fielded,
    Paired,
    Standing,
    baseline_inputs,
    field,
    pair,
    table,
    two_way_bootstrap,
)

MODULE = "343"
STARTS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
BENCH = (1, 2, 3, 4)
ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}),
    1: frozenset({"POR"}), 2: frozenset({"A"}), 3: frozenset({"DC"}), 4: frozenset({"E"}),
}


def _votes(value: float = 6.0, *, absent: Sequence[int] = ()) -> dict[int, float]:
    return {pid: value for pid in (*STARTS, *BENCH) if pid not in absent}


def _field(votes: Mapping[int, float], **over: object) -> Fielded:
    kwargs: dict[str, object] = {
        "module": MODULE, "starts": STARTS, "bench": BENCH, "votes": votes,
        "roles": ROLES, "rules": RULES, "sub_mode": "basic",
    }
    kwargs.update(over)
    return field(**kwargs)  # type: ignore[arg-type]


class TestTheRealizedScore:
    def test_a_full_eleven_is_the_eleven_summed(self) -> None:
        outcome = _field(_votes(6.0))

        assert outcome.fantapunti == pytest.approx(66.0)
        assert (outcome.malus, outcome.short, outcome.entered) == (0, 0, ())

    def test_the_ladder_turns_the_total_into_goals(self) -> None:
        """66 is `stlmt`, so a total on the threshold is exactly one goal — the boundary a
        ladder read off the wrong edge gets wrong and a mean never shows."""
        assert _field(_votes(6.0)).goals == 1
        assert _field(_votes(5.0)).goals == 0
        assert _field(_votes(6.6)).goals == 2

    def test_the_fielded_eleven_is_scored_and_not_the_submitted_one(self) -> None:
        """A substitute came on; the man he replaced contributes nothing, because he has no
        vote to contribute. Scoring `starts` instead would credit a player who did not play."""
        votes = _votes(6.0, absent=(80,)) | {2: 9.0}

        outcome = _field(votes)

        assert outcome.entered == (2,)
        assert outcome.fantapunti == pytest.approx(10 * 6.0 + 9.0)

    def test_a_malus_is_one_point_off_the_total(self) -> None:
        votes = _votes(6.0, absent=(40, 70, 2))

        outcome = _field(votes)

        assert outcome.malus == 1
        assert outcome.fantapunti == pytest.approx(11 * 6.0 - 1.0)

    def test_a_man_short_is_one_player_fewer(self) -> None:
        votes = _votes(6.0, absent=(20, 30, 2, 4))

        outcome = _field(votes)

        assert outcome.short == 1
        assert outcome.fantapunti == pytest.approx(10 * 6.0)

    def test_absence_is_the_complement_of_the_votes(self) -> None:
        """One source of truth for who played. Two — a vote map and an absence list — is two
        things the arms can disagree about, and the arms must not. The keeper has no vote and
        the bench holds exactly one reserve keeper, so the answer is not a disjunction."""
        outcome = _field({pid: 6.0 for pid in (*STARTS, *BENCH) if pid != 0})

        assert outcome.entered == (1,)
        assert outcome.fielded[0] == 1
        assert outcome.short == 0

    def test_the_two_modes_field_different_elevens_and_score_differently(self) -> None:
        """The acceptance criterion, and the reason `--sub-mode` is required rather than
        defaulted: a forward is missing, the bench holds a midfielder who fields only 352,
        so BASIC changes module for free and EASY pays the `-1`. Same absences, same votes,
        two realized totals — which is exactly what an unconfirmed mode would grade.
        """
        starts = (0, 11, 12, 13, 21, 31, 32, 22, 41, 42, 43)
        roles = {
            0: frozenset({"POR"}),
            11: frozenset({"DC"}), 12: frozenset({"DC"}), 13: frozenset({"DC"}),
            21: frozenset({"E"}), 22: frozenset({"E"}),
            31: frozenset({"C"}), 32: frozenset({"C"}),
            41: frozenset({"A"}), 42: frozenset({"A"}), 43: frozenset({"A"}),
            51: frozenset({"M"}),
        }
        votes = {pid: 6.0 for pid in (*starts, 51) if pid != 43}
        shared = {
            "module": "343", "starts": starts, "bench": (51,), "votes": votes,
            "roles": roles, "rules": RULES, "modules": ("343", "352"),
        }

        easy = field(**shared, sub_mode="easy")  # type: ignore[arg-type]
        basic = field(**shared, sub_mode="basic")  # type: ignore[arg-type]

        assert (easy.module, easy.malus) == ("343", 1)
        assert (basic.module, basic.malus) == ("352", 0)
        assert easy.fantapunti == pytest.approx(basic.fantapunti - 1.0)

    def test_a_vote_of_zero_is_a_vote(self) -> None:
        """`0.0` is a real fantavoto — a red card and a goal conceded get there — and a
        replay that read falsy as absent would substitute a man who played."""
        votes = _votes(6.0) | {80: 0.0}

        outcome = _field(votes)

        assert outcome.entered == ()
        assert outcome.fantapunti == pytest.approx(10 * 6.0)


class TestTheRoomsTable:
    def _room(self, goals_by_buyer: Mapping[str, int]) -> dict[str, Fielded]:
        return {
            buyer: Fielded(
                module=MODULE, fielded=(), entered=(), fantapunti=60.0 + n,
                goals=n, malus=0, short=0,
            )
            for buyer, n in goals_by_buyer.items()
        }

    def test_every_roster_meets_every_other(self) -> None:
        """Hand-computed: with goals 3/2/1, the top wins both, the middle splits, the bottom
        loses both — 6, 3 and 0 points over two fixtures each."""
        standings = table(self._room({"a": 3, "b": 2, "c": 1}))

        assert [(s.buyer, s.points) for s in standings] == [
            ("a", 3.0), ("b", 1.5), ("c", 0.0)
        ]

    def test_a_tie_is_a_point_each(self) -> None:
        standings = table(self._room({"a": 2, "b": 2}))

        assert [s.points for s in standings] == [1.0, 1.0]

    def test_it_is_per_fixture_so_room_sizes_are_comparable(self) -> None:
        """A 10-team room would otherwise weigh 9/7 of an 8-team one in a pooled corpus for
        no reason anyone chose."""
        small = table(self._room({chr(97 + i): 5 - i for i in range(4)}))
        large = table(self._room({chr(97 + i): 9 - i for i in range(8)}))

        assert small[0].points == large[0].points == 3.0
        assert max(s.points for s in (*small, *large)) <= 3.0

    def test_a_room_of_one_has_no_fixture(self) -> None:
        assert table(self._room({"a": 3})) == (
            Standing(buyer="a", fantapunti=63.0, goals=3, points=0.0),
        )

    def test_the_order_is_the_buyer_and_not_the_table(self) -> None:
        """A replay is diffed between runs; ordering by result makes every improvement look
        like a reshuffle."""
        standings = table(self._room({"z": 1, "a": 3}))

        assert [s.buyer for s in standings] == ["a", "z"]


class TestTheBaseline:
    def test_the_mean_is_of_the_last_five_and_not_of_the_season(self) -> None:
        mu, _p = baseline_inputs({7: [1.0, 1.0, 6.0, 6.0, 6.0, 6.0, 6.0]}, {})

        assert mu[7] == pytest.approx(6.0)

    def test_a_short_season_averages_what_there_is(self) -> None:
        mu, _p = baseline_inputs({7: [6.0, 8.0]}, {})

        assert mu[7] == pytest.approx(7.0)

    def test_a_player_with_no_vote_is_unknown_and_not_average(self) -> None:
        """Ranking an unknown at the mean is how a baseline flatters itself."""
        mu, p = baseline_inputs({7: []}, {})

        assert (mu[7], p[7]) == (0.0, 0.0)

    def test_p_is_binary_and_reads_the_latest_match_only(self) -> None:
        _mu, p = baseline_inputs({7: [6.0] * 10, 8: [6.0]}, {7: False, 8: True})

        assert (p[7], p[8]) == (0.0, 1.0)

    def test_the_window_is_the_specs_own_five(self) -> None:
        assert (BASELINE_WINDOW, BURN_IN) == (5, 5)


class TestPairing:
    def _standings(self, points: Mapping[str, float]) -> tuple[Standing, ...]:
        return tuple(
            Standing(buyer=b, fantapunti=60.0 + n, goals=1, points=n)
            for b, n in sorted(points.items())
        )

    def test_the_same_roster_is_joined_to_itself(self) -> None:
        rows = pair("r1", 7, self._standings({"a": 1.0}), self._standings({"a": 2.0}))

        assert rows[0].delta_points == pytest.approx(1.0)
        assert rows[0].delta_fantapunti == pytest.approx(1.0)

    def test_two_arms_that_fielded_different_rosters_are_refused(self) -> None:
        """Unpaired, the comparison is of two means measured on different weeks."""
        with pytest.raises(ValueError, match="different rosters"):
            pair("r1", 7, self._standings({"a": 1.0}), self._standings({"b": 2.0}))

    def test_a_missing_roster_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(ValueError, match="different rosters"):
            pair("r1", 7, self._standings({"a": 1.0, "b": 1.0}), self._standings({"a": 2.0}))


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

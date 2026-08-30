"""The target-price model, which decides what gets spent as real credits.

`db price` computes the only numbers in this repository that turn into money, and until
now nothing tested any of it. Not because it was hard — because every function in
`pricing.py` opened its own database session, so there was no way to hand it inputs. The
same shape `prices.py` and `pool.py` had before P11: a pure model with a query welded to
its front.

These tests are written against the pure functions the model *should* expose. Each one
pins a decision that is currently a branch in the middle of a 100-line function, and
several of them are decisions with a recorded reason and a named player behind them.
"""

from __future__ import annotations

import math
from typing import ClassVar

import pytest

from fantabot.application.pricing import (
    GOALKEEPER_MACRO,
    MIN_QI,
    PricingReport,
    RoleFade,
    TargetPriceRow,
    build_report,
    count_observations,
    discount_factors,
    fit_fades,
    macro_role,
    price_universe,
    training_pairs,
)
from fantabot.domain.shared.values import BiasRow, PlayerQuote, PriorStats


def _bias(**over: object) -> BiasRow:
    base: dict[str, object] = {
        "stagione": "2024/25",
        "id": "1",
        "nome": "Tizio",
        "squadra": "ATA",
        "role": "c",
        "qi": 10,
        "qa": 12,
        "delta": 2,
        "pct_delta": 20.0,
    }
    return BiasRow(**(base | over))  # type: ignore[arg-type]


class TestMacroRole:
    """The bucket a player's fade is fitted in. Two role systems, one output vocabulary."""

    def test_classic_maps_its_single_letter(self) -> None:
        assert macro_role("p", "classic") == GOALKEEPER_MACRO
        assert macro_role("d", "classic") == "DEF"
        assert macro_role("c", "classic") == "MID"
        assert macro_role("a", "classic") == "ATT"

    def test_mantra_takes_the_first_component_of_a_compound(self) -> None:
        """`DC;DD` is a defender who also plays right back. The first is his primary."""
        assert macro_role("DC;DD", "mantra") == "DEF"
        assert macro_role("W;A", "mantra") == "MID_ATT"

    def test_mantra_splits_wingers_and_trequartisti_from_midfield(self) -> None:
        """MID_ATT exists because those two fade differently from a `C`."""
        assert macro_role("C", "mantra") == "MID"
        assert macro_role("W", "mantra") == "MID_ATT"
        assert macro_role("T", "mantra") == "MID_ATT"
        assert macro_role("A", "mantra") == "ATT"

    def test_an_unknown_code_raises_rather_than_guessing_a_bucket(self) -> None:
        """A silent default would price a whole role off another role's fade."""
        with pytest.raises(KeyError):
            macro_role("ZZ", "mantra")


class TestFitFades:
    """Which observations teach the model, and which are refused."""

    @staticmethod
    def _cohort(n: int, *, role: str = "c", squadra: str = "ATA") -> tuple[list, dict]:
        """`n` players whose qa/qi ratio rises with their prior fantamedia."""
        rows = [
            _bias(id=str(i), role=role, squadra=squadra, qi=10, qa=10 + i % 7)
            for i in range(n)
        ]
        priors = {
            (str(i), "2023/24"): PriorStats(partite_giocate=30, media_fantavoto=6.0 + i % 7)
            for i in range(n)
        }
        return rows, priors

    def test_it_fits_a_role_with_enough_observations(self) -> None:
        rows, priors = self._cohort(40)
        fades = fit_fades(rows, priors, "classic")

        assert "MID" in fades
        assert isinstance(fades["MID"], RoleFade)

    def test_a_role_under_twenty_observations_gets_no_fade(self) -> None:
        """Below that the slope is noise, and a noisy slope moves real credits."""
        rows, priors = self._cohort(19)
        assert fit_fades(rows, priors, "classic") == {}

    def test_goalkeepers_never_teach_the_fade(self) -> None:
        rows, priors = self._cohort(40, role="p")
        assert GOALKEEPER_MACRO not in fit_fades(rows, priors, "classic")

    def test_a_player_the_platform_marked_worthless_is_excluded(self) -> None:
        """`qa == 0` makes `log(qa/qi)` a domain error, but that is not why.

        A player written down to zero mid-season is a data artefact, not evidence about
        how quotazioni fade -- and the appearance filter cannot catch him, because it
        reads his *prior* season. Goglichidze (6537, UDI) is the live case: qa 0 in
        2025/26 with 33 appearances in 2024/25, squarely inside the cohort.
        """
        rows, priors = self._cohort(40)
        rows.append(_bias(id="zero", qa=0))
        priors[("zero", "2023/24")] = PriorStats(partite_giocate=33, media_fantavoto=6.0)

        fit_fades(rows, priors, "classic")  # must not raise

    def test_a_thin_prior_season_does_not_teach_the_fade(self) -> None:
        """The regression-to-mean correlation was measured at -0.191 for 25-38 appearances
        and between -0.007 and -0.073 for thinner samples. Outside the range it is noise."""
        rows, priors = self._cohort(40)
        rows.extend(_bias(id=f"thin{i}", qi=10, qa=30) for i in range(40))
        priors.update(
            {(f"thin{i}", "2023/24"): PriorStats(partite_giocate=5, media_fantavoto=9.0)
             for i in range(40)}
        )

        with_thin = fit_fades(rows, priors, "classic")["MID"]
        without, priors_without = self._cohort(40)

        assert with_thin == fit_fades(without, priors_without, "classic")["MID"]

    def test_a_player_with_no_prior_season_is_skipped(self) -> None:
        rows, priors = self._cohort(40)
        rows.append(_bias(id="newcomer", qa=25))

        assert fit_fades(rows, priors, "classic")["MID"] == self._fit_of(40)

    @staticmethod
    def _fit_of(n: int) -> RoleFade:
        rows, priors = TestFitFades._cohort(n)
        return fit_fades(rows, priors, "classic")["MID"]

    def test_the_clamps_are_the_fifth_and_ninety_fifth_percentile(self) -> None:
        """A clamp keeps one breakout season from repricing a whole role."""
        rows, priors = self._cohort(40)
        fade = fit_fades(rows, priors, "classic")["MID"]

        ratios = sorted(math.log(r.qa / r.qi) for r in rows)
        assert fade.clamp_lo == ratios[int(0.05 * len(ratios))]
        assert fade.clamp_hi == ratios[int(0.95 * len(ratios)) - 1]


class TestRoleFadePrediction:
    FADE = RoleFade(slope=0.1, intercept=-0.5, clamp_lo=-0.2, clamp_hi=0.2)

    def test_a_prediction_inside_the_clamps_is_returned_as_fitted(self) -> None:
        assert self.FADE.predict_log_ratio(6.0) == pytest.approx(0.1)

    def test_a_prediction_below_the_floor_is_clamped_up(self) -> None:
        assert self.FADE.predict_log_ratio(0.0) == -0.2

    def test_a_prediction_above_the_ceiling_is_clamped_down(self) -> None:
        assert self.FADE.predict_log_ratio(100.0) == 0.2

    def test_the_factor_is_the_exponential_of_the_clamped_ratio(self) -> None:
        """The model is multiplicative: target = qi * factor. Fitted on log for symmetry."""
        assert self.FADE.predict_factor(6.0) == pytest.approx(math.exp(0.1))


class TestDiscountFactors:
    """A team-level median, and only for the two clubs on the allowlist.

    `TEAM_DISCOUNT_ALLOWLIST` is `{"NAP", "MIL"}` -- two clubs whose players were measured
    as systematically over-quoted. It is an allowlist rather than a computation over every
    club because a median over twenty players is a number for any club, and applying it to
    all of them would fit noise as a discount eighteen times over.
    """

    def test_the_factor_is_one_plus_the_median_percentage_delta(self) -> None:
        rows = [_bias(squadra="NAP", pct_delta=p) for p in (-20.0, -10.0, 0.0)]
        assert discount_factors(rows)["NAP"] == pytest.approx(0.9)

    def test_the_median_not_the_mean_because_one_breakout_must_not_move_a_club(self) -> None:
        rows = [_bias(squadra="NAP", pct_delta=p) for p in (-10.0, -10.0, 350.0)]
        assert discount_factors(rows)["NAP"] == pytest.approx(0.9)

    def test_a_club_that_is_not_on_the_allowlist_gets_no_factor(self) -> None:
        """Even with a strong signal. `ATA` here would take a 50% discount if it applied."""
        assert discount_factors([_bias(squadra="ATA", pct_delta=-50.0)]) == {}

    def test_an_allowlisted_club_with_no_observations_gets_no_factor(self) -> None:
        """A missing club must fall through to 1.0, not to a factor built from nothing."""
        assert "MIL" not in discount_factors([_bias(squadra="NAP", pct_delta=-10.0)])


class TestPriceUniverse:
    """What each flag means, which one wins, and the floor under every price."""

    #: A fade that doubles every price, so the arithmetic under test is visible.
    FADES: ClassVar[dict[str, RoleFade]] = {
        "MID": RoleFade(slope=0.0, intercept=math.log(2.0), clamp_lo=-9.0, clamp_hi=9.0)
    }

    @staticmethod
    def _quote(**over: object):  # type: ignore[no-untyped-def]
        
        base: dict[str, object] = {
            "stagione": "2026/27", "id": "1", "nome": "Tizio", "squadra": "NAP",
            "role": "c", "qi": 10, "qa": 10, "fvm": 0,
        }
        return PlayerQuote(**(base | over))  # type: ignore[arg-type]

    def _price(self, quote, priors=None, factors=None):  # type: ignore[no-untyped-def]
        return price_universe(
            [quote], priors or {}, self.FADES, factors or {}, "classic"
        )[0]

    def test_a_priced_player_gets_qi_times_the_fade(self) -> None:
        priors = {("1", "2025/26"): PriorStats(partite_giocate=30, media_fantavoto=6.0)}
        row = self._price(self._quote(), priors)

        assert row.target_price == 20
        assert row.flags == ""
        assert row.predicted_pct_delta == pytest.approx(100.0)

    def test_a_player_at_or_below_the_floor_is_never_faded(self) -> None:
        """Below qi 2 the percentage delta is dominated by the divisor, not the market."""
        row = self._price(self._quote(qi=MIN_QI))

        assert row.flags == "floor_qi"
        assert row.target_price == MIN_QI

    def test_the_floor_beats_every_other_reason_for_not_fading(self) -> None:
        """It is first in the chain, so a cheap keeper reads `floor_qi`, not `goalkeeper`."""
        row = self._price(self._quote(qi=1, role="p"))
        assert row.flags == "floor_qi"

    def test_a_goalkeeper_is_not_faded(self) -> None:
        row = self._price(self._quote(role="p"))
        assert row.flags == "goalkeeper_no_fade"
        assert row.target_price == 10

    def test_a_player_with_no_prior_season_is_not_faded(self) -> None:
        assert self._price(self._quote()).flags == "no_prior_data"

    def test_a_player_whose_prior_season_was_thin_is_not_faded(self) -> None:
        priors = {("1", "2025/26"): PriorStats(partite_giocate=3, media_fantavoto=9.0)}
        assert self._price(self._quote(), priors).flags == "thin_prior_sample_no_fade"

    def test_a_role_with_no_fitted_fade_is_not_faded(self) -> None:
        priors = {("1", "2025/26"): PriorStats(partite_giocate=30, media_fantavoto=6.0)}
        row = self._price(self._quote(role="d"), priors)

        assert row.flags == "no_role_fade_model"
        assert row.target_price == 10

    def test_a_team_factor_multiplies_and_is_recorded(self) -> None:
        row = self._price(self._quote(role="p"), factors={"NAP": 0.5})

        assert row.team_factor == 0.5
        assert row.target_price == 5
        assert "team_discount(NAP)" in row.flags

    def test_no_price_is_ever_zero(self) -> None:
        """A target of 0 is a bid nobody can place. The floor is one credit."""
        row = self._price(self._quote(qi=3, role="p"), factors={"NAP": 0.01})
        assert row.target_price == 1

    def test_rounding_is_pythons_and_that_is_load_bearing(self) -> None:
        """`round` is half-to-even. Pinning it because the qi_bias migration proved the
        difference is observable: one row differed at exactly .5 and an EXCEPT caught it."""
        row = self._price(self._quote(qi=5, role="p"), factors={"NAP": 0.5})
        assert row.target_price == 2  # 2.5 -> 2, not 3


class TestBuildReport:
    """What a run has to say, assembled without saying it.

    `run` printed as it went: fitted the fades and printed a Rich table, priced and
    printed two more, which is why the application layer reached `rich` at all. It
    returns this instead, and `interface/app.py` renders it.
    """

    #: `(qi, target, flags)`. Two expensive players carry a *small* move, so ranking by
    #: price and ranking by the credit difference disagree -- which is the whole claim of
    #: `test_the_biggest_movers_are_ranked_by_credits_not_by_ratio`. Every row used to
    #: have `qi=10`, so the two orderings coincided and that test passed against a
    #: `build_report` sorting by either. Found by mutation.
    SHAPE: ClassVar[list[tuple[int, int, str]]] = [
        (10, 30, ""),                      # +20
        (10, 20, "floor_qi"),              # +10
        (200, 205, ""),                    # +5,  and the second-highest price
        (10, 10, ""),                      # 0
        (10, 5, "team_discount(NAP)"),     # -5
        (10, 1, "floor_qi"),               # -9
        (300, 290, ""),                    # -10, and the highest price
    ]

    ROWS: ClassVar[list[TargetPriceRow]] = [
        TargetPriceRow(
            id=str(i), nome=f"P{i}", squadra="NAP", role="c", macro_role="MID",
            qi=qi, prior_media_fantavoto=6.0, predicted_pct_delta=0.0,
            team_factor=1.0, target_price=target, flags=flags,
        )
        for i, (qi, target, flags) in enumerate(SHAPE)
    ]

    #: Deliberately not `len(ROWS)`. The report carries what the upsert returned, and a
    #: test that passes the two in equal cannot tell them apart.
    STORED = 999

    def _report(self, top_n: int = 2) -> PricingReport:
        return build_report(
            system="mantra",
            fades={"MID": RoleFade(slope=0.1, intercept=0.0, clamp_lo=-1.0, clamp_hi=1.0)},
            observations={"MID": 42},
            team_factors={"NAP": 0.9},
            rows=self.ROWS,
            stored=self.STORED,
            top_n=top_n,
        )

    def test_each_fade_carries_the_count_it_was_fitted_from(self) -> None:
        """A slope from 20 observations and one from 400 are not the same claim, and the
        table is the only place an operator sees the difference."""
        assert [(f.role, f.observations) for f in self._report().fades] == [("MID", 42)]

    def test_the_biggest_movers_are_ranked_by_the_credit_difference(self) -> None:
        """Not by the price, and not by the ratio.

        What an operator is looking for is where the model disagrees with the platform by
        the most *credits* -- that is what a wrong number costs at the auction. The 200
        and 300 credit players in the fixture are the two most expensive and move the
        least, so ranking by price would put them first in both lists.
        """
        report = self._report()

        assert [(r.qi, r.target_price) for r in report.biggest_bumps] == [(10, 30), (10, 20)]
        assert [(r.qi, r.target_price) for r in report.biggest_cuts] == [(300, 290), (10, 1)]

    def test_top_n_bounds_both_lists(self) -> None:
        assert len(self._report(top_n=1).biggest_bumps) == 1
        assert len(self._report(top_n=1).biggest_cuts) == 1

    def test_asking_for_more_than_exist_returns_what_there_is(self) -> None:
        assert len(self._report(top_n=99).biggest_bumps) == len(self.ROWS)

    def test_flags_are_counted_by_kind_with_the_argument_stripped(self) -> None:
        """`team_discount(NAP)` and `team_discount(MIL)` are one kind of thing."""
        assert self._report().flag_counts == {"floor_qi": 2, "team_discount": 1}

    def test_it_reports_what_was_stored_rather_than_what_was_computed(self) -> None:
        """The upsert's return value. A row computed and not written is the bug this shows,
        so the fixture stores a count that is deliberately not `len(rows)`."""
        report = self._report()

        assert report.stored == 999
        assert report.stored != len(self.ROWS)


class TestObservationCounts:
    """The `n` beside a slope must be the rows that produced the slope.

    It was not. `run` recovered the count by re-running both training queries and
    re-applying the appearance filter -- but not the other two filters `fit_fades`
    applies, so a player written down to `qa == 0` was counted in `n` and excluded from
    the fit. A count that does not match its fit is worse than no count: it is the number
    an operator uses to decide whether to trust the slope.
    """

    @staticmethod
    def _rows_and_priors() -> tuple[list[BiasRow], dict[tuple[str, str], PriorStats]]:
        rows = [_bias(id=str(i), qi=10, qa=12) for i in range(25)]
        priors = {
            (str(i), "2023/24"): PriorStats(partite_giocate=30, media_fantavoto=6.0)
            for i in range(25)
        }
        return rows, priors

    def test_the_count_is_the_number_of_pairs_the_fit_used(self) -> None:
        rows, priors = self._rows_and_priors()
        assert count_observations(rows, priors, "classic") == {"MID": 25}

    def test_a_worthless_player_is_absent_from_the_count_as_he_is_from_the_fit(self) -> None:
        rows, priors = self._rows_and_priors()
        rows.append(_bias(id="zero", qa=0))
        priors[("zero", "2023/24")] = PriorStats(partite_giocate=33, media_fantavoto=6.0)

        assert count_observations(rows, priors, "classic") == {"MID": 25}

    def test_a_goalkeeper_is_absent_from_the_count_as_he_is_from_the_fit(self) -> None:
        rows, priors = self._rows_and_priors()
        rows.append(_bias(id="keeper", role="p"))
        priors[("keeper", "2023/24")] = PriorStats(partite_giocate=30, media_fantavoto=6.0)

        assert GOALKEEPER_MACRO not in count_observations(rows, priors, "classic")

    def test_it_agrees_with_the_fit_on_the_same_input(self) -> None:
        """The property that matters, asserted directly: one filter, not two."""
        rows, priors = self._rows_and_priors()
        rows.append(_bias(id="zero", qa=0))
        rows.append(_bias(id="thin", qa=30))
        priors[("zero", "2023/24")] = PriorStats(partite_giocate=33, media_fantavoto=6.0)
        priors[("thin", "2023/24")] = PriorStats(partite_giocate=2, media_fantavoto=9.0)

        counts = count_observations(rows, priors, "classic")
        pairs = training_pairs(rows, priors, "classic")

        assert counts == {role: len(ps) for role, ps in pairs.items()}

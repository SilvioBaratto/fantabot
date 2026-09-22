"""Same-team dependence: the fitted copula correlations, R, and the draws. Pure.

The fits are built so the answer is exact: a class whose two roles carry identical residuals
in every match has Pearson 1 before shrinkage, so its target is exactly `n/(n + n0)`, and the
copula rho is whatever reproduces that target through the two marginals (SPEC A25). The output
correlation is checked against a closed form, the arcsine law for two sign marginals. The
draws never pin a Monte Carlo number; they assert same-seed identity, exact proportionality
under a correlation of 1, and the support of a two-point marginal.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import _importgraph as G
import numpy as np
import pytest
from scipy.special import ndtri

from fantabot.domain.lineup.dependence import (
    Dependence,
    DependenceConfig,
    Floats,
    Member,
    Residual,
    correlation_matrix,
    draw,
    fit,
    output_correlation,
    repair,
    residuals,
)
from fantabot.domain.lineup.projection import Observation, Projection, Target

DAY = date(2026, 1, 4)
#: 100 distinct residuals, symmetric about 0.
ZS = [(k - 49.5) / 25.0 for k in range(100)]


def _match(k: int) -> date:
    return DAY + timedelta(days=7 * k)


def _paired(role_a: str, role_b: str, *, flip: bool = False) -> list[Residual]:
    """One `role_a` and one `role_b` of the same club in each of 100 matches, carrying the
    same residual (or its negative)."""
    out: list[Residual] = []
    for k, z in enumerate(ZS):
        out.append(Residual(macro=role_a, club="INT", played_on=_match(k), z=z))
        out.append(Residual(macro=role_b, club="INT", played_on=_match(k), z=-z if flip else z))
    return out


def _dependence(rho: dict[tuple[str, str], float], grid: list[float] | None = None) -> Dependence:
    marginal = np.array(sorted(grid if grid is not None else ZS), dtype=np.float64)
    marginal = (marginal - marginal.mean()) / marginal.std()
    return Dependence(
        rho=rho, pearson=rho, pairs={cls: 100 for cls in rho}, marginals={"*": marginal},
        pooled=0.0,
    )


class TestConfig:
    @pytest.mark.parametrize("n0", [-1.0, math.nan])
    def test_a_shrinkage_that_is_not_a_count_is_refused(self, n0: float) -> None:
        with pytest.raises(ValueError, match="shrink_pairs"):
            DependenceConfig(shrink_pairs=n0)

    def test_the_declared_prior(self) -> None:
        """100 pseudo-pairs of zero correlation (the operator, 2026-09-22)."""
        assert DependenceConfig().shrink_pairs == 100.0


def _standard(values: list[float] | Floats) -> Floats:
    out = np.sort(np.asarray(values, dtype=np.float64))
    return (out - out.mean()) / out.std()


class TestOutputCorrelation:
    """What Pearson the copula produces at rho, through two marginals."""

    @pytest.mark.parametrize("rho", [-0.6, -0.2, 0.0, 0.1, 0.3, 0.6])
    def test_two_signs_follow_the_arcsine_law(self, rho: float) -> None:
        """sign(Z1), sign(Z2) for normals at rho have correlation (2/π)·arcsin rho."""
        sign = np.array([-1.0, 1.0])
        assert output_correlation(rho, sign, sign) == pytest.approx(
            2 / math.pi * math.asin(rho), abs=1e-9
        )

    def test_gaussian_marginals_give_back_rho(self) -> None:
        m = 4000
        gaussian = _standard(ndtri((np.arange(1, m + 1) - 0.5) / m))
        assert output_correlation(0.3, gaussian, gaussian) == pytest.approx(0.3, abs=2e-3)

    def test_a_correlation_does_not_depend_on_the_marginals_scale(self) -> None:
        sign = np.array([-2.0, 2.0])
        assert output_correlation(0.3, sign, sign) == pytest.approx(
            2 / math.pi * math.asin(0.3), abs=1e-9
        )

    def test_a_skewed_marginal_attenuates(self) -> None:
        skewed = _standard([z**3 for z in ZS])
        assert 0 < output_correlation(0.3, skewed, skewed) < 0.3


class TestFit:
    def test_a_class_is_shrunk_toward_0_by_its_pair_count(self) -> None:
        """Identical residuals: Pearson 1 over 100 pairs, so the target is n/(n + n0) = ½."""
        dep = fit(_paired("DEF", "GK"))
        assert dep.pairs[("DEF", "GK")] == 100
        assert dep.pearson[("DEF", "GK")] == pytest.approx(0.5)

    def test_the_copula_rho_reproduces_the_class_s_pearson_through_its_marginals(
        self,
    ) -> None:
        """SPEC A25. Cubing the keepers' residuals skews their marginal: the copula needs a
        larger rho than the Pearson it must reproduce."""
        plain = _paired("DEF", "GK")
        warped = [
            Residual(r.macro, r.club, r.played_on, r.z**3 if r.macro == "GK" else r.z)
            for r in plain
        ]
        dep = fit(warped)
        rho, target = dep.correlation("DEF", "GK"), dep.pearson[("DEF", "GK")]
        assert output_correlation(rho, dep.marginal("DEF"), dep.marginal("GK")) == (
            pytest.approx(target, abs=1e-9)
        )
        assert rho > target > 0

    def test_without_shrinkage_identical_residuals_are_perfectly_coupled(self) -> None:
        dep = fit(_paired("DEF", "GK"), config=DependenceConfig(shrink_pairs=0.0))
        assert dep.pearson[("DEF", "GK")] == pytest.approx(1.0)
        assert dep.correlation("DEF", "GK") == pytest.approx(1.0, abs=1e-6)

    def test_a_negative_coupling_keeps_its_sign(self) -> None:
        dep = fit(_paired("DEF", "GK", flip=True))
        assert dep.pearson[("DEF", "GK")] == pytest.approx(-0.5)
        assert dep.correlation("DEF", "GK") < -0.5

    def test_a_class_is_unordered(self) -> None:
        dep = fit(_paired("GK", "DEF"))
        assert dep.correlation("GK", "DEF") == dep.correlation("DEF", "GK")

    def test_players_of_different_clubs_or_days_are_not_paired(self) -> None:
        rows = [
            Residual("DEF", "INT", DAY, 1.0),
            Residual("GK", "NAP", DAY, 1.0),
            Residual("GK", "INT", DAY + timedelta(days=1), 1.0),
            *_paired("MID", "ATT"),
        ]
        dep = fit(rows)
        assert dep.pairs.get(("DEF", "GK"), 0) == 0
        assert dep.correlation("DEF", "GK") == 0.0

    def test_a_same_role_class_does_not_depend_on_who_is_listed_first(self) -> None:
        """Two defenders at z and z + 1: one orientation reads Pearson 1, both read
        (v - ¼)/(v + ¼) with v the variance of z — the pair has no first player."""
        rows = [
            Residual("DEF", "INT", _match(k), z + shift) for k, z in enumerate(ZS)
            for shift in (0.0, 1.0)
        ]
        v = float(np.var(ZS))
        dep = fit(rows, config=DependenceConfig(shrink_pairs=0.0))
        assert dep.pearson[("DEF", "DEF")] == pytest.approx((v - 0.25) / (v + 0.25))
        assert dep.pooled == pytest.approx((v - 0.25) / (v + 0.25))

    def test_a_cross_role_class_does_not_depend_on_who_is_listed_first(self) -> None:
        plain = [
            Residual(r.macro, r.club, r.played_on, r.z**3 if r.macro == "GK" else r.z)
            for r in _paired("DEF", "GK")
        ]
        mixed = [
            row
            for k in range(len(ZS))
            for row in (plain[2 * k : 2 * k + 2] if k % 2 else plain[2 * k : 2 * k + 2][::-1])
        ]
        assert fit(mixed).pearson == fit(plain).pearson

    def test_a_same_role_class_counts_each_pair_once(self) -> None:
        dep = fit(_paired("DEF", "DEF"))
        assert dep.pairs[("DEF", "DEF")] == 100
        assert dep.pearson[("DEF", "DEF")] == pytest.approx(0.5)

    def test_the_pooled_correlation_is_the_raw_pearson_over_every_pair(self) -> None:
        dep = fit([*_paired("DEF", "GK"), *_paired("MID", "ATT", flip=True)])
        assert dep.pooled == pytest.approx(0.0, abs=1e-9)

    def test_each_marginal_is_standardized_and_sorted(self) -> None:
        rows = _paired("DEF", "GK")
        # Fed in descending order: sorting is the fit's job, not the caller's.
        warped = [
            Residual(r.macro, r.club, r.played_on, 3.0 + 2.0 * r.z**3) for r in reversed(rows)
        ]
        marginal = fit(warped).marginal("GK")
        assert marginal.mean() == pytest.approx(0.0, abs=1e-12)
        assert marginal.std() == pytest.approx(1.0)
        assert list(marginal) == sorted(marginal)

    def test_a_role_s_marginal_is_its_own_residuals(self) -> None:
        plain = _paired("DEF", "GK")
        warped = [
            Residual(r.macro, r.club, r.played_on, r.z**3 if r.macro == "GK" else r.z)
            for r in plain
        ]
        dep = fit(warped)
        assert np.allclose(dep.marginal("GK"), _standard([z**3 for z in ZS]))
        assert np.allclose(dep.marginal("DEF"), _standard(ZS))

    def test_a_role_with_no_residuals_draws_from_the_pool_of_every_role(self) -> None:
        dep = fit(_paired("DEF", "GK"))
        assert np.array_equal(dep.marginal("ATT"), dep.marginals["*"])

    def test_too_little_to_fit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="too thin"):
            fit([Residual("DEF", "INT", DAY, 1.0)])


class TestCorrelationMatrix:
    MEMBERS = (
        Member("DEF", "INT", 6.0, 1.0),
        Member("GK", "INT", 6.0, 1.0),
        Member("DEF", "NAP", 6.0, 1.0),
        Member("GK", None, 6.0, 1.0),
    )

    def test_same_club_pairs_take_their_class_and_everyone_else_is_independent(self) -> None:
        r = correlation_matrix(self.MEMBERS, _dependence({("DEF", "GK"): 0.2}))
        expected = np.eye(4)
        expected[0, 1] = expected[1, 0] = 0.2
        assert np.array_equal(r, expected)

    def test_an_unknown_club_is_independent_even_of_another_unknown_club(self) -> None:
        members = (Member("DEF", None, 6.0, 1.0), Member("GK", None, 6.0, 1.0))
        r = correlation_matrix(members, _dependence({("DEF", "GK"): 0.2}))
        assert np.array_equal(r, np.eye(2))

    def test_r_is_psd_with_a_unit_diagonal_even_when_the_classes_are_not(self) -> None:
        """Three players pairwise at -0.9 is no correlation matrix (an eigenvalue of -0.8)."""
        members = tuple(Member("DEF", "INT", 6.0, 1.0) for _ in range(3))
        r = correlation_matrix(members, _dependence({("DEF", "DEF"): -0.9}))
        assert np.linalg.eigvalsh(r).min() >= -1e-12
        assert np.array_equal(np.diag(r), np.ones(3))
        assert np.allclose(r, r.T)

    def test_a_matrix_that_is_already_psd_is_left_exactly_alone(self) -> None:
        m = np.array([[1.0, 0.3, 0.1], [0.3, 1.0, 0.2], [0.1, 0.2, 1.0]])
        assert np.array_equal(repair(m), m)


class TestDraw:
    def test_the_same_seed_gives_the_same_draws(self) -> None:
        dep = _dependence({("DEF", "GK"): 0.3})
        members = TestCorrelationMatrix.MEMBERS
        a = draw(members, dep, rng=np.random.default_rng(7), n=200)
        b = draw(members, dep, rng=np.random.default_rng(7), n=200)
        assert a.shape == (200, 4)
        assert np.array_equal(a, b)

    def test_one_role_with_twice_the_spread_draws_twice_the_deviation(self) -> None:
        """Correlation 1 makes the two players' normals one draw, so the only difference
        left is sigma_tilde: the deviations are exactly proportional."""
        members = (Member("DEF", "INT", 6.0, 0.5), Member("DEF", "INT", 5.0, 1.0))
        scores = draw(
            members, _dependence({("DEF", "DEF"): 1.0}), rng=np.random.default_rng(1), n=500
        )
        assert np.allclose(scores[:, 1] - 5.0, 2.0 * (scores[:, 0] - 6.0))
        assert np.std(scores[:, 0] - 6.0) > 0

    def test_draws_come_from_the_role_s_marginal_scaled_by_sigma_tilde(self) -> None:
        dep = _dependence({}, grid=[-1.0, 1.0])
        scores = draw((Member("MID", "INT", 6.0, 1.5),), dep, rng=np.random.default_rng(3), n=500)
        assert set(np.round(scores[:, 0], 12)) == {4.5, 7.5}

    def test_the_marginal_is_the_player_s_own_role_s(self) -> None:
        grids = {"*": np.array([-1.0, 1.0]), "GK": np.array([-2.0, 0.5, 0.5, 0.5, 0.5])}
        dep = Dependence(rho={}, pearson={}, pairs={}, marginals=grids, pooled=0.0)
        scores = draw((Member("GK", None, 6.0, 1.0),), dep, rng=np.random.default_rng(3), n=500)
        assert set(np.round(scores[:, 0], 12)) <= {4.0, 6.5}

    def test_same_club_draws_move_together_and_other_clubs_do_not(self) -> None:
        members = (
            Member("DEF", "INT", 6.0, 1.0),
            Member("GK", "INT", 6.0, 1.0),
            Member("GK", "NAP", 6.0, 1.0),
        )
        scores = draw(
            members, _dependence({("DEF", "GK"): 0.9}), rng=np.random.default_rng(5), n=500
        )
        c = np.corrcoef(scores.T)
        assert c[0, 1] > 0.7
        assert abs(c[0, 2]) < 0.2

    def test_the_draw_couples_two_signs_by_the_arcsine_law(self) -> None:
        """At rho = ½ two sign marginals correlate at (2/π)·arcsin ½ = ⅓."""
        members = (Member("DEF", "INT", 0.0, 1.0), Member("GK", "INT", 0.0, 1.0))
        dep = _dependence({("DEF", "GK"): 0.5}, grid=[-1.0, 1.0])
        scores = draw(members, dep, rng=np.random.default_rng(13), n=500)
        assert np.corrcoef(scores.T)[0, 1] == pytest.approx(1 / 3, abs=0.12)

    def test_the_draw_keeps_the_mean_and_the_spread(self) -> None:
        dep = _dependence({})
        scores = draw((Member("DEF", "INT", 6.0, 2.0),), dep, rng=np.random.default_rng(11), n=500)
        assert scores[:, 0].mean() == pytest.approx(6.0, abs=0.3)
        assert scores[:, 0].std() == pytest.approx(2.0, rel=0.15)


class TestResiduals:
    def test_a_residual_is_standardized_by_sigma_tilde(self) -> None:
        players = {1: Target(1, "DEF", 10, "INT")}
        projections = {
            1: Projection(1, 6.0, 4.0, 4.0, 6.0, 1.0, 0.1, 3.0, 1.0)
        }
        rows = [Observation(1, DAY, 8.0, 10, "INT")]
        (r,) = residuals(rows, projections, players)
        assert r == Residual("DEF", "INT", DAY, 1.0)

    def test_an_appearance_without_a_resolved_club_or_a_projection_is_left_out(self) -> None:
        players = {1: Target(1, "DEF", 10, "INT")}
        projections = {1: Projection(1, 6.0, 4.0, 4.0, 6.0, 1.0, 0.1, 3.0, 1.0)}
        rows = [Observation(1, DAY, 8.0, 10, None), Observation(2, DAY, 8.0, 10, "INT")]
        assert residuals(rows, projections, players) == []


def test_dependence_reads_no_clock() -> None:
    clock_reads = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}
    assert clock_reads & G.names_used("fantabot.domain.lineup.dependence") == set()

"""The opponent's effect on a player's fantavoto: a within-player fit, and the adjustment it gives.

Two layers. `fit_roles` is the estimator, tested on synthetic rows whose answer is known. `adjust`
is the pipeline around it (strength features for every historical appearance, the fit, then the
upcoming opponent), tested on `_league_world`'s four-club league with an effect **planted
exactly**: each score is `player level + beta * x`, where `x` comes from the plain-loop
re-derivation in `_league_world`. With no noise the fit must return `beta` itself, so a feature
that leaks (a prior map that includes the row's own season, a cutoff that admits the day's own
results) or a join that is off by one recovers some other number and the test fails.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from _league_world import (
    CODES,
    fixtures,
    opens,
    plain_expected,
    player_id,
    start_of,
    valuations,
)

from fantabot.domain.lineup import matchup as matchup_module
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.matchup import (
    MAX_ABS_BETA,
    MIN_ROLE_ROWS,
    RoleEffect,
    TrainingRow,
    adjust,
    delta,
    fit_roles,
)
from fantabot.domain.lineup.pairing import Pairing
from fantabot.domain.lineup.scoring import Appearance
from fantabot.domain.shared.club_names import code_for

# ----------------------------------------------------------------------- the estimator


def _rows(
    beta: float,
    *,
    macro: str = "DEF",
    players: int = 12,
    seasons: tuple[str, ...] = ("2023/24", "2024/25"),
    per: int = 14,
    level: Any = lambda p, s: 3.0 + 0.2 * p + 1.5 * s,
    x_of: Any = None,
) -> list[TrainingRow]:
    """`per` appearances for each of `players` players in each season, `y = level + beta * x`."""
    out: list[TrainingRow] = []
    for p in range(players):
        for si, season in enumerate(seasons):
            for j in range(per):
                x = x_of(p, si, j) if x_of else 1.0 + ((7 * p + 3 * j + 5 * si) % 13) / 10.0
                out.append(TrainingRow(p, season, macro, level(p, si) + beta * x, x))
    return out


class TestTheWithinEstimator:
    @pytest.mark.parametrize("beta", [-1.07, -0.25, -0.003])
    def test_it_recovers_a_planted_slope_exactly(self, beta: float) -> None:
        fit = fit_roles(_rows(beta), min_rows=10)
        assert fit.refused == {}
        assert fit.effects["DEF"].beta == pytest.approx(beta, abs=1e-9)

    def test_the_centre_is_the_mean_of_the_role_s_own_opponent_strengths(self) -> None:
        rows = _rows(-0.5)
        fit = fit_roles(rows, min_rows=10)
        assert fit.effects["DEF"].centre == pytest.approx(sum(r.opponent_ppg for r in rows) / len(rows))
        assert fit.effects["DEF"].rows == len(rows)

    def test_a_player_effect_correlated_with_the_opponent_does_not_bias_it(self) -> None:
        """Strong players meet weak opponents: pooled OLS reads the player level as an
        opponent effect. Demeaning within the player-season is what removes it."""
        beta = -0.8

        def x_of(p: int, si: int, j: int) -> float:
            return 2.0 - 0.1 * p + ((j * 3) % 7) / 20.0  # high level (p) goes with low x

        rows = _rows(beta, x_of=x_of, level=lambda p, s: 2.0 * p)
        xs = [r.opponent_ppg for r in rows]
        ys = [r.score for r in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        pooled = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum(
            (x - mx) ** 2 for x in xs
        )
        assert abs(pooled - beta) > 0.5, "the control: pooled OLS must be biased here"
        fit = fit_roles(rows, min_rows=10)
        assert fit.effects["DEF"].beta == pytest.approx(beta, abs=1e-9)

    def test_the_group_is_the_player_season_and_not_the_player(self) -> None:
        """A player's level can move between seasons while the opponents he met also moved."""
        beta = -0.6

        def x_of(p: int, si: int, j: int) -> float:
            return (0.9 if si == 0 else 1.7) + ((p + j) % 5) / 25.0

        rows = _rows(beta, x_of=x_of, level=lambda p, s: 4.0 + 6.0 * s)
        assert fit_roles(rows, min_rows=10).effects["DEF"].beta == pytest.approx(beta, abs=1e-9)

    def test_a_single_appearance_player_season_informs_the_centre_and_not_the_slope(self) -> None:
        base = _rows(-0.7)
        lone = TrainingRow(999, "2024/25", "DEF", 40.0, 3.0)
        with_lone = fit_roles([*base, lone], min_rows=10).effects["DEF"]
        without = fit_roles(base, min_rows=10).effects["DEF"]
        assert with_lone.beta == pytest.approx(without.beta, abs=1e-12)
        assert with_lone.centre != without.centre
        assert with_lone.rows == without.rows + 1

    def test_each_role_is_its_own_fit(self) -> None:
        rows = _rows(-0.9, macro="DEF") + _rows(-0.4, macro="GK")
        fit = fit_roles(rows, min_rows=10)
        assert fit.effects["DEF"].beta == pytest.approx(-0.9, abs=1e-9)
        assert fit.effects["GK"].beta == pytest.approx(-0.4, abs=1e-9)


class TestItFailsClosedPerRole:
    def test_too_few_rows_is_refused_with_the_count(self) -> None:
        rows = _rows(-0.5, players=1, seasons=("2024/25",), per=MIN_ROLE_ROWS - 1)
        fit = fit_roles(rows)
        assert "DEF" not in fit.effects
        assert str(MIN_ROLE_ROWS - 1) in fit.refused["DEF"]

    def test_exactly_the_minimum_is_enough(self) -> None:
        rows = _rows(-0.5, players=10, seasons=("2024/25",), per=MIN_ROLE_ROWS // 10)
        assert len(rows) == MIN_ROLE_ROWS
        assert "DEF" in fit_roles(rows).effects

    def test_a_stronger_opponent_that_raises_the_score_is_refused(self) -> None:
        fit = fit_roles(_rows(+0.5), min_rows=10)
        assert fit.effects == {}
        assert "sign" in fit.refused["DEF"]

    def test_a_flat_slope_is_refused_too(self) -> None:
        # Multiples of a half, so every group's mean is exact and the slope is exactly 0.0.
        fit = fit_roles(_rows(0.0, level=lambda p, s: 3.0 + 0.5 * p + 1.5 * s), min_rows=10)
        assert "DEF" not in fit.effects

    def test_an_implausibly_steep_slope_is_refused(self) -> None:
        fit = fit_roles(_rows(-(MAX_ABS_BETA + 0.5)), min_rows=10)
        assert "DEF" not in fit.effects
        assert "implausible" in fit.refused["DEF"]

    def test_a_slope_just_inside_the_bound_is_kept(self) -> None:
        assert "DEF" in fit_roles(_rows(-(MAX_ABS_BETA - 0.1)), min_rows=10).effects

    def test_no_variation_in_the_opponent_is_refused_and_does_not_divide_by_zero(self) -> None:
        fit = fit_roles(_rows(-0.5, x_of=lambda p, si, j: 1.4), min_rows=10)
        assert "DEF" not in fit.effects
        assert "variation" in fit.refused["DEF"]

    def test_one_refused_role_leaves_the_others(self) -> None:
        fit = fit_roles(_rows(+0.5, macro="GK") + _rows(-0.9, macro="DEF"), min_rows=10)
        assert set(fit.effects) == {"DEF"}
        assert set(fit.refused) == {"GK"}

    def test_no_rows_is_an_empty_fit(self) -> None:
        fit = fit_roles([])
        assert fit.effects == {} and fit.refused == {}


class TestTheAdjustmentIsTheSlopeTimesTheDistanceFromTheCentre:
    def test_it_is_zero_at_the_centre_and_signed_by_the_slope_away_from_it(self) -> None:
        effect = RoleEffect(beta=-1.0, centre=1.36, rows=900)
        assert delta(effect, 1.36) == 0.0
        assert delta(effect, 2.36) == pytest.approx(-1.0)  # a stronger opponent costs points
        assert delta(effect, 0.86) == pytest.approx(+0.5)  # a weaker one adds them


# -------------------------------------------------------------------------- the pipeline

COMPLETED = ("2022/23", "2023/24", "2024/25")
CURRENT = "2025/26"
BETA = {"DEF": -0.9, "GK": -0.4}
#: Players k = 0, 1 of every club are defenders, k = 2, 3 goalkeepers.
MACRO = {player_id(c, k): "DEF" if k < 2 else "GK" for c in range(4) for k in range(4)}
MIN_ROWS = 40


def _vals(seasons: tuple[str, ...] = (*COMPLETED, CURRENT)) -> dict[str, dict[int, Valuation]]:
    return {s: valuations(per_club=4) for s in seasons}


def _fx(current_rounds: int = 3, trimmed: str | None = None) -> dict[str, list[Fixture]]:
    out = {s: fixtures(s, shift=i) for i, s in enumerate(COMPLETED)}
    out[CURRENT] = fixtures(CURRENT, shift=3, rounds=current_rounds)
    if trimmed:
        out[trimmed] = fixtures(trimmed, shift=COMPLETED.index(trimmed), rounds=5)
    return out


def _appearance(pid: int, f: Fixture) -> HistoryAppearance:
    return HistoryAppearance(
        player_id=pid, role="D", fixture=f, scored=Appearance(voto_fc=0.0), fantavoto_fc=None
    )


def _history(
    vals: dict[str, dict[int, Valuation]],
    fx: dict[str, list[Fixture]],
    *,
    beta: dict[str, float] = BETA,
    complete: tuple[str, ...] = COMPLETED,
    junk: tuple[str, ...] = (),
) -> tuple[list[tuple[HistoryAppearance, float]], dict[str, list[float]]]:
    """Every player in every match of the given seasons, scored `level + beta * x_true`.
    Returns the rows and, per role, the true `x` of each, for the centre. Seasons in `junk`
    carry scores that would wreck the fit if anything read them."""
    scored: list[tuple[HistoryAppearance, float]] = []
    xs: dict[str, list[float]] = {"DEF": [], "GK": []}
    for si, season in enumerate((*COMPLETED, CURRENT)):
        for f in fx.get(season, []):
            for club, opp in ((f.home, f.away), (f.away, f.home)):
                for c in range(4):
                    if code_for(club) != CODES[c]:
                        continue
                    for k in range(4):
                        pid = player_id(c, k)
                        if season in junk:
                            scored.append((_appearance(pid, f), 999.0 * (1 + k)))
                            continue
                        others = [t for t in complete if t != season]
                        x = plain_expected(
                            vals, fx, map_seasons=others, season=season, club=code_for(opp),
                            before=opens(fx[season], f.giornata),
                        )
                        role = MACRO[pid]
                        if season != CURRENT:
                            xs[role].append(x)
                        scored.append(
                            (_appearance(pid, f), 4.0 + 0.1 * pid + 0.7 * si + beta[role] * x)
                        )
    return scored, xs


def _adjust(
    scored: list[tuple[HistoryAppearance, float]],
    vals: dict[str, dict[int, Valuation]],
    fx: dict[str, list[Fixture]],
    pairings: dict[int, Pairing],
    *,
    cmday: int = 4,
    as_of: date | None = None,
    macro: dict[int, str] = MACRO,
) -> Any:
    return adjust(
        scored=scored, valuations=vals, fixtures=fx, macro=macro, pairings=pairings,
        season=CURRENT, cmday=cmday, as_of=as_of or start_of(CURRENT) + timedelta(days=25),
        min_role_rows=MIN_ROWS,
    )


ALP_BRA = Pairing("ALP", "BRA")
DEL_CHA = Pairing("DEL", "CHA")
DEF_ALP, GK_ALP, DEF_DEL = player_id(0, 0), player_id(0, 2), player_id(3, 1)


def _expected_delta(role: str, opp: str, xs: list[float], vals: Any, fx: Any, as_of: date) -> float:
    live = plain_expected(
        vals, fx, map_seasons=list(COMPLETED), season=CURRENT, club=opp, before=as_of
    )
    return BETA[role] * (live - sum(xs) / len(xs))


class TestThePipelineRecoversAPlantedEffect:
    def _world(self) -> tuple[Any, Any, Any, dict[str, list[float]]]:
        vals, fx = _vals(), _fx()
        scored, xs = _history(vals, fx)
        return scored, vals, fx, xs

    def test_each_player_is_adjusted_by_his_role_s_slope_at_the_upcoming_opponent(self) -> None:
        scored, vals, fx, xs = self._world()
        as_of = start_of(CURRENT) + timedelta(days=25)
        pairings = {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA, DEF_DEL: DEL_CHA}
        out = _adjust(scored, vals, fx, pairings)
        assert set(out.adjustments) == set(pairings)
        for pid, opp in ((DEF_ALP, "BRA"), (GK_ALP, "BRA"), (DEF_DEL, "CHA")):
            want = _expected_delta(MACRO[pid], opp, xs[MACRO[pid]], vals, fx, as_of)
            got = out.adjustments[pid]
            assert got.delta == pytest.approx(want, abs=1e-9)
            assert (got.own, got.opponent) == (pairings[pid].own, opp)

    def test_the_note_names_the_slopes_and_the_head_count(self) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA, 12345: ALP_BRA})
        assert "DEF -0.90" in out.note and "GK -0.40" in out.note
        assert "2 of 3" in out.note

    def test_the_note_counts_the_appearances_and_the_completed_seasons_behind_the_slopes(
        self,
    ) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        # The current season's appearances are in `scored` and train nothing.
        trained = sum(1 for appearance, _ in scored if appearance.fixture.season in COMPLETED)
        assert 0 < trained < len(scored)
        assert f"({trained} appearances over {len(COMPLETED)} completed seasons)" in out.note

    def test_nobody_unpriced_means_no_unknown_clause(self) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA})
        assert "unknown" not in out.note and "refused" not in out.note

    def test_a_player_with_no_pairing_is_left_alone(self) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        assert set(out.adjustments) == {DEF_ALP}

    def test_a_player_with_no_macro_role_is_left_alone_and_is_not_blamed_on_a_slope(self) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {777: ALP_BRA, DEF_ALP: ALP_BRA})
        assert set(out.adjustments) == {DEF_ALP}
        assert "1 with no macro role" in out.note
        assert "no usable slope" not in out.note

    def test_an_opponent_the_season_has_no_club_for_is_left_alone_and_named(self) -> None:
        scored, vals, fx, _ = self._world()
        out = _adjust(scored, vals, fx, {DEF_ALP: Pairing("ALP", "XXX")})
        assert out.adjustments == {}
        assert "1 against an unknown club" in out.note
        assert "unknown: XXX" in out.note

    def test_each_unknown_club_is_named_once_and_in_alphabetical_order(self) -> None:
        """Sixteen players face eight unknown clubs, two each and in no particular order: a set
        of eight strings is in alphabetical order by chance 1 time in 40,320."""
        scored, vals, fx, _ = self._world()
        unknown = ["QQQ", "BBB", "MMM", "XXX", "DDD", "ZZZ", "KKK", "FFF"]
        pairings = {
            pid: Pairing(CODES[pid // 10], unknown[(7 * n) % 8])
            for n, pid in enumerate(MACRO)
        }
        out = _adjust(scored, vals, fx, pairings)
        assert out.adjustments == {}
        assert "16 against an unknown club" in out.note
        assert f"unknown: {', '.join(sorted(unknown))}" in out.note
        assert all(out.note.count(code) == 1 for code in unknown)

    def test_rows_whose_club_is_neither_end_of_the_match_are_dropped_from_the_fit(self) -> None:
        scored, vals, fx, xs = self._world()
        stray = player_id(0, 0)
        odd = [
            (_appearance(stray, f), -500.0)
            for f in fx[COMPLETED[0]]
            if "ALP" not in (code_for(f.home), code_for(f.away))
        ]
        assert odd, "the control needs rows that do not belong to the player's club"
        out = _adjust([*scored, *odd], vals, fx, {DEF_ALP: ALP_BRA})
        as_of = start_of(CURRENT) + timedelta(days=25)
        assert out.adjustments[DEF_ALP].delta == pytest.approx(
            _expected_delta("DEF", "BRA", xs["DEF"], vals, fx, as_of), abs=1e-9
        )


class TestNothingFromTheFutureOrTheCurrentSeasonTrains:
    def test_results_on_or_after_as_of_cannot_move_an_adjustment(self) -> None:
        vals = _vals()
        fx = _fx(current_rounds=6)
        as_of = opens(fx[CURRENT], 4)  # round 4 is the cutoff: it and everything later is future
        scored, _ = _history(vals, _fx(current_rounds=3))
        pairings = {DEF_ALP: ALP_BRA}
        real = _adjust(scored, vals, fx, pairings, as_of=as_of, cmday=4)

        scrambled = {
            s: [
                replace(f, home_goals=f.away_goals + 1, away_goals=f.home_goals)
                if f.played_on >= as_of
                else f
                for f in rows
            ]
            for s, rows in fx.items()
        }
        other = _adjust(scored, vals, scrambled, pairings, as_of=as_of, cmday=4)
        assert other.adjustments[DEF_ALP] == real.adjustments[DEF_ALP]

    def test_current_season_rows_do_not_train_the_slope(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        wrecking = [
            (_appearance(DEF_ALP, f), 900.0) for f in fx[CURRENT] if "ALP" in (
                code_for(f.home), code_for(f.away)
            )
        ]
        assert wrecking
        base = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        more = _adjust([*scored, *wrecking], vals, fx, {DEF_ALP: ALP_BRA})
        assert more.adjustments[DEF_ALP].delta == base.adjustments[DEF_ALP].delta

    def test_a_season_with_missing_rounds_is_not_a_training_season(self) -> None:
        trimmed = COMPLETED[1]
        vals, fx = _vals(), _fx(trimmed=trimmed)
        complete = tuple(s for s in COMPLETED if s != trimmed)
        scored, xs = _history(vals, fx, complete=complete, junk=(trimmed,))
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        as_of = start_of(CURRENT) + timedelta(days=25)
        live = plain_expected(
            vals, fx, map_seasons=list(complete), season=CURRENT, club="BRA", before=as_of
        )
        want = BETA["DEF"] * (live - sum(xs["DEF"]) / len(xs["DEF"]))
        assert out.adjustments[DEF_ALP].delta == pytest.approx(want, abs=1e-9)

    def test_the_note_counts_only_the_seasons_that_could_train(self) -> None:
        """Four seasons are read, one of them short of rounds and one of them the current: two
        trained, and the note must not say four or three."""
        trimmed = COMPLETED[1]
        vals, fx = _vals(), _fx(trimmed=trimmed)
        complete = tuple(s for s in COMPLETED if s != trimmed)
        scored, _ = _history(vals, fx, complete=complete, junk=(trimmed,))
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        trained = sum(1 for appearance, _ in scored if appearance.fixture.season in complete)
        assert f"({trained} appearances over 2 completed seasons)" in out.note


class TestTheSameInputsGiveTheSameBits:
    """`fit_value_map` sums in the order it is handed its tables, and `adjust` finds the
    seasons in a *set*, whose order follows PYTHONHASHSEED. Unsorted, the same inputs give
    deltas that differ in the last bit from one process to the next (3 distinct digests over
    12 seeds on `test_lineup_matchup`'s league). The hourly job is a new process every time."""

    #: Eight completed seasons, so a set of their names is in ascending order by chance with
    #: probability 1 in 40,320 and not 1 in 6.
    MANY = tuple(f"{y}/{(y + 1) % 100:02d}" for y in range(2015, 2023))

    def _many(self) -> tuple[dict[str, dict[int, Valuation]], dict[str, list[Fixture]]]:
        vals = {s: valuations(per_club=4) for s in (*self.MANY, CURRENT)}
        fx = {s: fixtures(s, shift=i) for i, s in enumerate(self.MANY)}
        fx[CURRENT] = fixtures(CURRENT, shift=3, rounds=3)
        return vals, fx

    def test_every_value_map_is_fitted_on_seasons_in_ascending_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vals, fx = self._many()
        seen: list[list[str]] = []
        real = matchup_module.fit_value_map

        def spy(tables: Any) -> Any:
            tables = list(tables)
            seen.append([table.season for table in tables])
            return real(tables)

        monkeypatch.setattr(matchup_module, "fit_value_map", spy)
        _adjust([], vals, fx, {DEF_ALP: ALP_BRA})
        # One live map over all eight, and one per season over the other seven.
        assert sorted(len(order) for order in seen) == [7] * len(self.MANY) + [len(self.MANY)]
        assert all(order == sorted(order) for order in seen)

    def test_the_order_the_seasons_arrive_in_changes_nothing(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        pairings = {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA, DEF_DEL: DEL_CHA}
        forward = _adjust(scored, vals, fx, pairings)
        backward = _adjust(
            scored, dict(reversed(vals.items())), dict(reversed(fx.items())), pairings
        )
        assert forward.adjustments, "the control: something was priced"
        assert backward == forward  # `==` on floats: the bits, not an approximation


class TestWhyNobodyWasAdjusted:
    def test_a_lineup_with_no_matchday_adjusts_nobody_and_says_so(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA}, cmday=0)
        assert out.adjustments == {}
        assert "matchday" in out.note

    def test_no_pairings_says_so(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        out = _adjust(scored, vals, fx, {})
        assert out.adjustments == {}
        assert "pairing" in out.note

    def test_a_round_already_played_is_not_an_upcoming_one(self) -> None:
        """The lineup has not advanced yet: the DTO still describes the round that finished."""
        vals = _vals()
        fx = _fx(current_rounds=4)
        scored, _ = _history(vals, _fx(current_rounds=3))
        as_of = start_of(CURRENT) + timedelta(days=25)  # round 4 was played on days 21-22
        played = {code_for(f.home): code_for(f.away) for f in fx[CURRENT] if f.giornata == 4}
        own, opp = next(iter(played.items()))
        pid = next(p for p in MACRO if vals[CURRENT][p].squadra == own)
        out = _adjust(scored, vals, fx, {pid: Pairing(own, opp)}, cmday=4, as_of=as_of)
        assert out.adjustments == {}
        assert "already played" in out.note

    def test_the_same_pairing_is_fine_when_that_round_is_still_to_come(self) -> None:
        vals = _vals()
        fx = _fx(current_rounds=4)
        scored, _ = _history(vals, _fx(current_rounds=3))
        as_of = opens(fx[CURRENT], 4)  # round 4 has not started
        played = {code_for(f.home): code_for(f.away) for f in fx[CURRENT] if f.giornata == 4}
        own, opp = next(iter(played.items()))
        pid = next(p for p in MACRO if vals[CURRENT][p].squadra == own)
        out = _adjust(scored, vals, fx, {pid: Pairing(own, opp)}, cmday=4, as_of=as_of)
        assert pid in out.adjustments

    def test_without_a_completed_season_there_is_nothing_to_fit_on(self) -> None:
        vals, fx = _vals((CURRENT,)), {CURRENT: fixtures(CURRENT, shift=3, rounds=3)}
        out = _adjust([], vals, fx, {DEF_ALP: ALP_BRA})
        assert out.adjustments == {}
        assert "completed season" in out.note

    def test_a_single_completed_season_cannot_price_itself_and_nothing_is_fitted(self) -> None:
        """Its value map would have to come from the other completed seasons, of which there
        are none: its rows are dropped and the roles have nothing to fit."""
        vals, fx = _vals(), _fx()
        only = COMPLETED[0]
        scored, _ = _history(vals, fx, complete=(only,), junk=COMPLETED)
        vals = {s: v for s, v in vals.items() if s not in COMPLETED[1:]}
        fx = {s: f for s, f in fx.items() if s not in COMPLETED[1:]}
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        assert out.adjustments == {}
        assert "no role has a usable slope" in out.note

    def test_a_pairing_for_a_player_the_caller_did_not_ask_about_is_still_priced_here(
        self,
    ) -> None:
        """`adjust` prices whatever pairings it is handed; narrowing them to the roster that
        has a projection is the application's decision (`test_lineup_matchup.py`)."""
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA})
        assert set(out.adjustments) == {DEF_ALP, GK_ALP}

    def test_a_season_with_no_squad_values_for_this_year_is_named(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx)
        out = _adjust(scored, {**vals, CURRENT: {}}, fx, {DEF_ALP: ALP_BRA})
        assert out.adjustments == {}
        assert CURRENT in out.note

    def test_a_refused_role_leaves_its_players_alone_and_the_others_adjusted(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx, beta={"DEF": -0.9, "GK": +0.4})
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA, GK_ALP: ALP_BRA})
        assert set(out.adjustments) == {DEF_ALP}
        assert "GK" in out.note and "sign" in out.note
        assert "1 in a role with no usable slope" in out.note
        assert "no macro role" not in out.note

    def test_every_role_refused_is_a_named_reason(self) -> None:
        vals, fx = _vals(), _fx()
        scored, _ = _history(vals, fx, beta={"DEF": +0.9, "GK": +0.4})
        out = _adjust(scored, vals, fx, {DEF_ALP: ALP_BRA})
        assert out.adjustments == {}
        assert "no role" in out.note

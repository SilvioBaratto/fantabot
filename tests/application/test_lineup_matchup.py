"""The opponent adjustment in the plan: off unless asked for, and then only ever additive.

Three promises, each with its own class:

* **Off is today.** With `FANTABOT_LINEUP_OPPONENT` unset, empty or anything but an explicit
  yes, μ is the projection's own number to the last bit, the outcome is field-for-field the
  one it was, and carrying pairings in the inputs changes nothing. The setting is read at the
  point of use (`matchup_enabled`), so it can be flipped between two hourly runs. What counts
  as an explicit yes, in each layer the setting is read from, is
  `tests/adapters/test_config_live_flag.py`'s and is not repeated here.
* **On is additive and visible.** A paired player's μ moves by exactly the recorded
  adjustment *before* the sub-aware term, so the XI, the bench and the evaluator all see it;
  the history the dependence is fitted on does not.
* **A failure costs the adjustment and never the plan**, and says so by exception *type*.

The league is `_league_world`'s four clubs, with an effect planted exactly into every role's
scores, so "on" has something real to find.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from _league_world import CODES, fixtures, opens, plain_expected, start_of
from _paths import GOLDEN

from fantabot.application import lineup_projection
from fantabot.application.lineup_planner import LineupInputs, inputs_from_lineup, plan_lineups
from fantabot.application.lineup_projection import (
    DEFAULT_HALF_LIFE_DAYS,
    ProjectionOutcome,
    ProjectionReport,
    _seasons,
    _targets,
    matchup_enabled,
    plan_projection,
    projected_from,
)
from fantabot.config import LINEUP_NEWS_VAR, LINEUP_OPPONENT_VAR
from fantabot.domain.asta.roles import macro_role
from fantabot.domain.lineup import matchup
from fantabot.domain.lineup.choose import Budget
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.pairing import Pairing
from fantabot.domain.lineup.projection import ProjectionConfig, observations, project
from fantabot.domain.lineup.scoring import Appearance, ScoringRules
from fantabot.domain.lineup.value import sub_aware
from fantabot.domain.shared.values import SentimentRow

SEASON = "2026/27"
COMPLETED = ("2023/24", "2024/25", "2025/26")
#: Round 3 has been played and round 4 is the lineup's.
AS_OF = start_of(SEASON) + timedelta(days=25)

RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=0.0,
    decisive_goal=0.0, threshold=66.0, steps=(6.0, 6.0, 6.0, 6.0),
)
SMALL = Budget(k=3, lambdas=(0.0,), node_budget=200, m=2, work=8_000)

ROLES: dict[int, tuple[str, ...]] = {
    1: ("POR",), 2: ("POR",),
    3: ("DC",), 4: ("DC",), 5: ("DC",), 14: ("DC",),
    6: ("E",), 7: ("E",), 8: ("C",), 9: ("C", "M"), 15: ("C",),
    10: ("A",), 11: ("A",), 12: ("A",), 13: ("A",),
}
LISTONE_ONLY: dict[int, tuple[str, ...]] = {90: ("A",), 91: ("DC",)}
CLASSIC = {1: "P", 2: "P", 3: "D", 4: "D", 5: "D", 14: "D", 6: "C", 7: "C", 8: "C", 9: "C", 15: "C"}
INDEXCOMPARE = {pid: 50.0 for pid in ROLES} | {13: 100.0, 12: 1.0}
MACRO = {pid: macro_role(";".join(codes), "mantra") for pid, codes in ROLES.items()}
BETA = {"GK": -0.4, "DEF": -0.9, "MID": -0.3, "ATT": -0.5}
#: Round 4 of the double round robin: BRA-ALP and DEL-CHA.
PARTNER = {0: 1, 1: 0, 2: 3, 3: 2}
PAIRINGS = {pid: Pairing(CODES[pid % 4], CODES[PARTNER[pid % 4]]) for pid in ROLES}


def _valuations() -> dict[int, Valuation]:
    return {pid: Valuation(qi=10 + pid, squadra=CODES[pid % 4]) for pid in ROLES}


def _world() -> tuple[dict[str, dict[int, Valuation]], dict[str, list[Fixture]]]:
    vals = {s: _valuations() for s in (*COMPLETED, SEASON)}
    fx = {s: fixtures(s, shift=i) for i, s in enumerate(COMPLETED)}
    fx[SEASON] = fixtures(SEASON, shift=3, rounds=3)
    return vals, fx


def _rows_with_x(
    vals: dict[str, dict[int, Valuation]], fx: dict[str, list[Fixture]]
) -> list[tuple[HistoryAppearance, float]]:
    """Everyone plays every match of his club, scored `6 + a little + beta_role * x_true`,
    each with the opponent strength `x_true` it was scored from (the plain-loop one)."""
    out: list[tuple[HistoryAppearance, float]] = []
    for season in (*COMPLETED, SEASON):
        others = [s for s in COMPLETED if s != season]
        for f in fx[season]:
            home, away = f.home.upper()[:3], f.away.upper()[:3]
            for pid, valuation in vals[season].items():
                club = valuation.squadra
                if club not in (home, away):
                    continue
                if (pid == 13 and f.giornata % 2 == 0) or (pid == 9 and f.giornata % 3 == 0):
                    continue  # the presence prior needs players who sometimes do not play
                opp = away if club == home else home
                x = plain_expected(
                    vals, fx, map_seasons=others, season=season, club=opp,
                    before=opens(fx[season], f.giornata),
                )
                voto = 6.0 + 0.1 * (pid % 5) + BETA[MACRO[pid]] * x
                out.append(
                    (
                        HistoryAppearance(
                            player_id=pid, role=CLASSIC.get(pid, "A"), fixture=f,
                            scored=Appearance(voto_fc=voto), fantavoto_fc=voto,
                        ),
                        x,
                    )
                )
    return out


def _rows(
    vals: dict[str, dict[int, Valuation]], fx: dict[str, list[Fixture]]
) -> list[HistoryAppearance]:
    return [row for row, _ in _rows_with_x(vals, fx)]


def _centres(vals: dict[str, dict[int, Valuation]], fx: dict[str, list[Fixture]]) -> dict[str, float]:
    """Each role's mean opponent strength over the completed seasons' appearances: the
    opponent that should move nobody."""
    by_role: dict[str, list[float]] = {}
    for row, x in _rows_with_x(vals, fx):
        if row.fixture.season in COMPLETED:
            by_role.setdefault(MACRO[row.player_id], []).append(x)
    return {role: sum(xs) / len(xs) for role, xs in by_role.items()}


class League:
    """`domain.lineup.history.LineupHistory` over the four-club league."""

    def __init__(self) -> None:
        self.vals, self.fx = _world()
        self.rows = _rows(self.vals, self.fx)

    def appearances(
        self, player_ids: Collection[int], *, seasons: Collection[str], before: date
    ) -> list[HistoryAppearance]:
        return [
            r for r in self.rows
            if r.player_id in set(player_ids)
            and r.fixture.season in set(seasons)
            and r.fixture.played_on < before
        ]

    def roles(self, season: str) -> dict[int, tuple[str, ...]]:
        return dict(ROLES) | dict(LISTONE_ONLY) if season == SEASON else {}

    def valuations(self, season: str) -> dict[int, Valuation]:
        return self.vals.get(season, {})

    def fixtures(self, season: str) -> list[Fixture]:
        return list(self.fx.get(season, []))

    def calculated_scores(self, league_id: int) -> list[float]:
        return []

    def latest_sentiment(self, player_ids: Collection[int]) -> dict[int, SentimentRow]:
        return {}


def _inputs(**over: object) -> LineupInputs:
    fields: dict[str, object] = {
        "roster_ids": list(ROLES),
        "roles_by_id": {pid: list(codes) for pid, codes in ROLES.items()},
        "fvmma_by_id": dict(INDEXCOMPARE),
        "modules": ["343"],
        "competition": 311681,
        "mday": 3,
        "cmday": 4,
        "tid": 10000003,
        "bench_size": 2,
        "pairings": dict(PAIRINGS),
    }
    return LineupInputs(**{**fields, **over})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _small_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The league is 18 appearances a player a season, not 21,000: the floor is scaled down so
    the smallest role (two goalkeepers, 36 rows) can be fitted. The floor's own behaviour is
    `test_matchup.py`'s."""
    monkeypatch.setattr(matchup, "MIN_ROLE_ROWS", 30)


def _plan(on: bool | None, history: League | None = None, **over: object) -> ProjectionOutcome:
    return plan_projection(
        _inputs(**over), history or League(), as_of=AS_OF, rules=RULES, budget=SMALL,
        matchup_on=on,
    )


class TestOffIsToday:
    def test_nothing_is_adjusted_and_nothing_is_said(self) -> None:
        outcome = _plan(False)
        assert outcome.adjustments == {}
        assert outcome.matchup_note == ""

    def test_mu_is_the_projection_s_own_to_the_last_bit(self) -> None:
        history = League()
        inputs = _inputs()
        targets = _targets(inputs, history.roles(SEASON), history.valuations(SEASON))
        seasons = _seasons(SEASON)
        rows = history.appearances(sorted(targets), seasons=seasons, before=AS_OF)
        projections = project(
            observations(rows, rules=RULES, valuations={s: history.valuations(s) for s in seasons}),
            targets, as_of=AS_OF, config=ProjectionConfig(DEFAULT_HALF_LIFE_DAYS),
        )
        outcome = _plan(False, history)
        assert {line.player_id: line.mu for line in outcome.lines} == {
            pid: projections[pid].mu for pid in ROLES
        }

    def test_carrying_pairings_changes_nothing_while_it_is_off(self) -> None:
        assert _plan(False) == _plan(False, pairings={})

    def test_the_default_plan_with_pairings_is_the_one_without(self) -> None:
        """The setting is read at the point of use; the conftest empties it for every test."""
        history = League()
        with_pairings = plan_projection(
            _inputs(), history, as_of=AS_OF, rules=RULES, budget=SMALL
        )
        without = plan_projection(
            _inputs(pairings={}), history, as_of=AS_OF, rules=RULES, budget=SMALL
        )
        assert with_pairings == without
        assert with_pairings.matchup_note == ""


class TestOnIsAdditiveAndVisible:
    def test_every_paired_player_is_adjusted_and_the_outcome_says_so(self) -> None:
        outcome = _plan(True)
        assert set(outcome.adjustments) == set(ROLES)
        assert outcome.matchup_note.startswith("opponent adjustment on")
        for macro, beta in BETA.items():
            assert f"{macro} {beta:+.2f}" in outcome.matchup_note

    def test_mu_moves_by_exactly_the_recorded_adjustment(self) -> None:
        history = League()
        off, on = _plan(False, history), _plan(True, history)
        before = {line.player_id: line.mu for line in off.lines}
        for line in on.lines:
            assert line.mu == pytest.approx(
                before[line.player_id] + on.adjustments[line.player_id].delta, abs=1e-12
            )

    def test_the_planted_effect_is_what_moves_it(self) -> None:
        """Each delta is the planted slope times the opponent's distance from the role's
        centre, both re-derived here by plain loops. The slopes are negative, so a man facing
        a side stronger than his role's average loses points and one facing a weaker side
        gains them: `sign(delta) == sign(beta * (x - centre))`."""
        history = League()
        centres = _centres(history.vals, history.fx)
        on = _plan(True, history)
        gains: set[bool] = set()
        for pid, adjustment in on.adjustments.items():
            role = MACRO[pid]
            x = plain_expected(
                history.vals, history.fx, map_seasons=list(COMPLETED), season=SEASON,
                club=adjustment.opponent, before=AS_OF,
            )
            assert adjustment.opponent_ppg == pytest.approx(x, abs=1e-12)
            assert adjustment.delta == pytest.approx(BETA[role] * (x - centres[role]), abs=1e-9)
            assert (adjustment.delta < 0.0) == (x > centres[role])
            gains.add(adjustment.delta > 0.0)
        assert gains == {True, False}, "the control: the world has both a gain and a loss"

    def test_the_value_is_the_sub_aware_term_of_the_adjusted_mu(self) -> None:
        on = _plan(True)
        expected = sub_aware(
            {line.player_id: line.mu for line in on.lines},
            {line.player_id: line.p for line in on.lines},
        )
        assert {line.player_id: line.value for line in on.lines} == pytest.approx(expected)

    def test_the_matcher_ranks_the_modules_on_those_values(self) -> None:
        on = _plan(True)
        values = {line.player_id: line.value for line in on.lines}
        matcher = plan_lineups(replace(_inputs(), fvmma_by_id=values))
        assert {(p.module, p.starts) for p in matcher} <= {(p.module, p.starts) for p in on.plans}

    def test_a_player_without_a_pairing_keeps_his_mu(self) -> None:
        history = League()
        off = {line.player_id: line.mu for line in _plan(False, history).lines}
        pairings = {pid: p for pid, p in PAIRINGS.items() if pid != 3}
        on = _plan(True, history, pairings=pairings)
        assert 3 not in on.adjustments
        assert {line.player_id: line.mu for line in on.lines}[3] == off[3]

    def test_a_pairing_for_someone_not_on_the_roster_is_not_an_adjustment(self) -> None:
        on = _plan(True, pairings={**PAIRINGS, 999: Pairing("ALP", "BRA")})
        assert 999 not in on.adjustments
        assert f"{len(ROLES)} of {len(ROLES)} paired players adjusted" in on.matchup_note
        assert "without a pairing" not in on.matchup_note

    def test_a_rostered_player_the_projection_has_no_line_for_is_neither_priced_nor_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A roster id with no role codes has no target and so no projection, and nothing would
        carry his adjustment: `roster` (the men the projection has a line for) bounds what is
        priced and counted, not `inputs.roster_ids`.

        `plan_lineups` refuses such a man (`RosterIncomplete`) after the adjustment has been
        priced, so the matcher is handed the roster without him; the question under test is
        only what `_matchups` was asked about.
        """
        ghost = 77
        real = lineup_projection.plan_lineups
        monkeypatch.setattr(
            lineup_projection,
            "plan_lineups",
            lambda i: real(replace(i, roster_ids=[pid for pid in i.roster_ids if pid != ghost])),
        )
        on = _plan(
            True,
            roster_ids=[*ROLES, ghost],
            pairings={**PAIRINGS, ghost: Pairing("ALP", "BRA")},
        )
        assert ghost not in on.adjustments
        assert f"{len(ROLES)} of {len(ROLES)} paired players adjusted" in on.matchup_note
        assert "without a pairing" not in on.matchup_note

    def test_a_rostered_player_with_no_pairing_is_counted_in_the_note(self) -> None:
        pairings = {pid: p for pid, p in PAIRINGS.items() if pid not in (3, 4)}
        on = _plan(True, pairings=pairings)
        assert "2 roster player(s) without a pairing" in on.matchup_note

    def test_the_evaluator_is_handed_the_adjusted_mu_and_the_unadjusted_dependence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[Any] = []
        real = lineup_projection.choose_plan

        def spy(plan_inputs: Any, **kwargs: Any) -> Any:
            seen.append(plan_inputs)
            return real(plan_inputs, **kwargs)

        monkeypatch.setattr(lineup_projection, "choose_plan", spy)
        history = League()
        off, on = _plan(False, history), _plan(True, history)
        assert len(seen) == 2
        off_inputs, on_inputs = seen
        assert on_inputs.mu == pytest.approx({line.player_id: line.mu for line in on.lines})
        assert off_inputs.mu == pytest.approx({line.player_id: line.mu for line in off.lines})
        assert on_inputs.mu != off_inputs.mu
        assert on_inputs.sigma_tilde == off_inputs.sigma_tilde
        assert dict(on_inputs.dependence.rho) == dict(off_inputs.dependence.rho)


class TestOnButItCannotRun:
    def test_a_failure_inside_it_costs_the_adjustment_and_not_the_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(**_: Any) -> Any:
            raise RuntimeError("postgresql://operator:hunter2@db/fantabot")

        history = League()
        off = _plan(False, history)
        monkeypatch.setattr(matchup, "adjust", boom)
        on = _plan(True, history)
        assert on.adjustments == {}
        assert on.chosen is not None, "the plan is still made"
        assert on.lines == off.lines
        assert "RuntimeError" in on.matchup_note and "skipped" in on.matchup_note

    def test_the_note_carries_the_exception_type_and_never_its_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(**_: Any) -> Any:
            raise ValueError("postgresql://operator:hunter2@db/fantabot")

        monkeypatch.setattr(matchup, "adjust", boom)
        assert "hunter2" not in _plan(True).matchup_note

    def test_a_lineup_with_no_pairings_leaves_mu_alone_and_says_why(self) -> None:
        history = League()
        on = _plan(True, history, pairings={})
        assert on.adjustments == {}
        assert on.lines == _plan(False, history, pairings={}).lines
        assert "pairing" in on.matchup_note and "skipped" in on.matchup_note

    def test_a_lineup_with_no_matchday_is_not_adjusted(self) -> None:
        on = _plan(True, cmday=0)
        assert on.adjustments == {}
        assert "matchday" in on.matchup_note

    def test_a_classic_lega_is_skipped_by_name(self) -> None:
        got = lineup_projection._matchups(
            _inputs(fmt="classic"),
            enabled=True, scored=[], valuations={}, fixtures={}, targets={}, roster=[],
            season=SEASON, as_of=AS_OF,
        )
        assert got == matchup.Matchups(
            adjustments={},
            note="opponent adjustment on, skipped: classic is not what it was measured on",
        )

    def test_a_history_that_does_not_pair_with_its_scores_is_contained_too(self) -> None:
        """`scored` reaches `_matchups` unmaterialised and is drained inside the boundary, so
        the mismatch a strict `zip` raises costs the adjustment, by type, and never the plan."""
        got = lineup_projection._matchups(
            _inputs(),
            enabled=True, scored=zip([League().rows[0]], [], strict=True), valuations={},
            fixtures={}, targets={}, roster=list(ROLES), season=SEASON, as_of=AS_OF,
        )
        assert got == matchup.Matchups(
            adjustments={}, note="opponent adjustment on, skipped: ValueError"
        )


class TestTheRunRecordCarriesIt:
    def _warnings(self, on: bool, **over: object) -> tuple[str, ...]:
        outcome = _plan(on, **over)
        report = ProjectionReport(
            baseline=outcome.plans, projection=outcome, names={}, competition=311681
        )
        return projected_from(report, for_submit=False).warnings

    def test_on_the_note_is_a_warning_beside_the_others(self) -> None:
        notes = [w for w in self._warnings(True) if w.startswith("opponent adjustment on")]
        assert len(notes) == 1

    @pytest.mark.parametrize(
        ("over", "reason"),
        [({"cmday": 0}, "matchday"), ({"pairings": {}}, "pairing")],
    )
    def test_a_skipped_note_is_a_warning_too(self, over: dict[str, object], reason: str) -> None:
        """The record is where an operator tells "skipped, and why" from "the setting is off";
        carrying only the note of a run that adjusted something would make every failure read
        as the second."""
        notes = [
            w for w in self._warnings(True, **over) if w.startswith("opponent adjustment on")
        ]
        assert len(notes) == 1
        assert notes[0].startswith("opponent adjustment on, skipped") and reason in notes[0]

    def test_a_contained_failure_is_a_warning_naming_only_its_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(**_: Any) -> Any:
            raise RuntimeError("postgresql://operator:hunter2@db/fantabot")

        monkeypatch.setattr(matchup, "adjust", boom)
        notes = [w for w in self._warnings(True) if w.startswith("opponent adjustment on")]
        assert len(notes) == 1 and "RuntimeError" in notes[0] and "hunter2" not in notes[0]

    def test_off_there_is_no_note_and_the_warnings_do_not_depend_on_the_pairings(self) -> None:
        assert not [w for w in self._warnings(False) if w.startswith("opponent adjustment")]
        outcome = _plan(False, pairings={})
        report = ProjectionReport(
            baseline=outcome.plans, projection=outcome, names={}, competition=311681
        )
        assert projected_from(report, for_submit=False).warnings == self._warnings(False)


class TestTheSetting:
    """The gate as the use case meets it. What the setting may say, in the environment and in
    `.env`, and what the conftest leaves of the operator's own, is
    `tests/adapters/test_config_live_flag.py`'s."""

    def test_the_variable_is_the_one_the_operator_types(self) -> None:
        assert LINEUP_OPPONENT_VAR == "FANTABOT_LINEUP_OPPONENT"

    def test_settings_declare_it_as_an_optional_string(self) -> None:
        from fantabot.config import Settings

        assert Settings.model_fields["fantabot_lineup_opponent"].default is None

    def test_the_plan_follows_its_own_variable_and_no_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One off and one on, with no override. The off is the news variable alone being on,
        which a gate reading its sibling would get backwards, and which `test_config_live_flag`
        cannot tell because it sets both variables to the same value."""
        history = League()

        def plan() -> ProjectionOutcome:
            return plan_projection(_inputs(), history, as_of=AS_OF, rules=RULES, budget=SMALL)

        monkeypatch.setenv(LINEUP_NEWS_VAR, "on")
        sibling_only = plan()
        assert sibling_only.adjustments == {} and sibling_only.matchup_note == ""

        monkeypatch.setenv(LINEUP_NEWS_VAR, "")
        monkeypatch.setenv(LINEUP_OPPONENT_VAR, "on")
        assert set(plan().adjustments) == set(ROLES)

    def test_an_explicit_argument_beats_the_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(LINEUP_OPPONENT_VAR, "on")
        assert matchup_enabled(False) is False
        monkeypatch.setenv(LINEUP_OPPONENT_VAR, "off")
        assert matchup_enabled(True) is True


GOLDEN_BODY = json.loads((GOLDEN / "lineup" / "teamLineup_read.json").read_text())
GOLDEN_SETTINGS = json.loads((GOLDEN / "lineup" / "lineup_settings.json").read_text())


class TestThePlannerCarriesThePairings:
    def _inputs(self, rows: list[dict[str, Any]] | None = None) -> LineupInputs:
        inputs, _ = inputs_from_lineup(
            GOLDEN_BODY["teamLineupDto"], rows if rows is not None else GOLDEN_BODY["lineUpInfo"],
            GOLDEN_SETTINGS, 311681, tid=10000003,
        )
        return inputs

    def test_each_row_s_pairing_is_read_from_the_dto(self) -> None:
        inputs = self._inputs()
        assert len(inputs.pairings) == 30
        assert inputs.pairings[6534] == Pairing(own="TOR", opponent="UDI")

    def test_a_row_that_does_not_resolve_has_no_pairing_and_the_rest_do(self) -> None:
        rows = [dict(r) for r in GOLDEN_BODY["lineUpInfo"]]
        rows[0]["teamH"] = ""
        inputs = self._inputs(rows)
        assert int(rows[0]["pid"]) not in inputs.pairings
        assert len(inputs.pairings) == 29

    def test_a_caller_that_builds_inputs_by_hand_gets_an_empty_default(self) -> None:
        bare = LineupInputs(
            roster_ids=[], roles_by_id={}, fvmma_by_id={}, modules=[], competition=1, mday=1,
            cmday=1, tid=1, bench_size=1,
        )
        assert bare.pairings == {}

    def test_the_default_plan_does_not_depend_on_them(self) -> None:
        inputs = self._inputs()
        assert plan_lineups(inputs) == plan_lineups(replace(inputs, pairings={}))

    def test_a_row_with_junk_in_every_field_costs_only_its_own_pairing(self) -> None:
        rows = [dict(r) for r in GOLDEN_BODY["lineUpInfo"]]
        for key in ("teamH", "teamA", "tname", "hoaw", "tidOp"):
            rows[0][key] = object()
        assert len(self._inputs(rows).pairings) == 29

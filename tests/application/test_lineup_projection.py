"""The projection plan: history -> μ, p, sigma_tilde -> the sub-aware value -> the matcher.

A literal 15-man roster fields 343 with four forwards for three slots: `GOOD` scores well and
plays every week, `FLASHY` carries the platform's own high `indexCompare`, scores badly and
plays half the time. The two models then disagree about the XI, which is the point of the
phase. Two more players sit in the listone and nobody's roster, because the prior is fitted
on the population and read back for the roster.

The history is a fake implementing `domain.lineup.history.LineupHistory` — no session, no
socket. `as_of` is passed, never read from a clock.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, timedelta

import pytest

from fantabot.application.lineup_planner import LineupInputs
from fantabot.application.lineup_projection import (
    ProjectionOutcome,
    _chosen,
    plan_projection,
    substitution_cap,
)
from fantabot.domain.lineup.choose import Budget
from fantabot.domain.lineup.errors import BenchIncomplete, NoFieldableModule
from fantabot.domain.lineup.history import Fixture, HistoryAppearance, Valuation
from fantabot.domain.lineup.scoring import Appearance, ScoringRules
from fantabot.domain.shared.values import SentimentRow

AS_OF = date(2026, 9, 22)
SEASON = "2026/27"
CLUB = "INT"
GOOD, FLASHY = 12, 13

RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=0.0,
    decisive_goal=0.0, threshold=66.0, steps=(6.0, 6.0, 6.0, 6.0),
)

#: 343 needs POR, {B,DC}, DC, DC, E, C, {C,M}, E, {A,W}, {A,PC}, {A,W}.
ROLES: dict[int, tuple[str, ...]] = {
    1: ("POR",), 2: ("POR",),
    3: ("DC",), 4: ("DC",), 5: ("DC",), 14: ("DC",),
    6: ("E",), 7: ("E",), 8: ("C",), 9: ("C", "M"), 15: ("C",),
    10: ("A",), 11: ("A",), GOOD: ("A",), FLASHY: ("A",),
}
#: Listone players nobody owns: the population the prior is fitted on is not the roster.
LISTONE_ONLY: dict[int, tuple[str, ...]] = {90: ("A",), 91: ("DC",)}
#: The platform's own value: `FLASHY` is its pick, `GOOD` its last man.
INDEXCOMPARE = {pid: 50.0 for pid in ROLES} | {FLASHY: 100.0, GOOD: 1.0}


def _fixture(giornata: int) -> Fixture:
    return Fixture(
        season=SEASON, giornata=giornata, played_on=AS_OF - timedelta(days=7 * (9 - giornata)),
        home="Inter", away="Lecce", home_goals=1, away_goals=0,
    )


FIXTURES = [_fixture(g) for g in range(1, 9)]


#: Player 7 is an `E` — macro MID, so the map says `C` — who was fielded at the back from
#: giornata 5 on. `match_grain` records what he was actually played as, and it is the latest
#: that counts.
MOVED = 7


def _appearance(pid: int, fixture: Fixture, voto: float) -> HistoryAppearance:
    role = {
        1: "P", 2: "P", 3: "D", 4: "D", 5: "D", 14: "D",
        6: "C", 7: "C", 8: "C", 9: "C", 15: "C",
    }.get(pid, "A")
    if pid == MOVED and fixture.giornata >= 5:
        role = "D"
    return HistoryAppearance(
        player_id=pid, role=role, fixture=fixture,
        scored=Appearance(voto_fc=voto), fantavoto_fc=voto,
    )


def _history_rows() -> list[HistoryAppearance]:
    """Everyone plays all eight at about 6.0; `GOOD` scores 7/8, and `FLASHY` 4.5/5.5 in
    three of the last six — a weak player who plays half the time."""
    rows: list[HistoryAppearance] = []
    for k, fixture in enumerate(FIXTURES):
        for pid in ROLES:
            if pid == FLASHY and k % 2:
                continue
            if pid == GOOD:
                voto = 8.0 if k % 2 else 7.0
            elif pid == FLASHY:
                voto = 5.5 if k % 4 else 4.5
            else:
                voto = 6.5 if k % 2 else 5.5
            rows.append(_appearance(pid, fixture, voto))
    return rows


class FakeHistory:
    """`domain.lineup.history.LineupHistory`, from literals, recording what was asked."""

    def __init__(self, sentiment: dict[int, SentimentRow] | None = None) -> None:
        self.rows = _history_rows()
        self.sentiment = sentiment or {}
        self.asked: dict[str, object] = {}

    def appearances(
        self, player_ids: Collection[int], *, seasons: Collection[str], before: date
    ) -> list[HistoryAppearance]:
        self.asked["ids"] = sorted(player_ids)
        self.asked["seasons"] = tuple(seasons)
        self.asked["before"] = before
        wanted = set(player_ids)
        return [
            row for row in self.rows
            if row.player_id in wanted
            and row.fixture.season in set(seasons)
            and row.fixture.played_on < before
        ]

    def roles(self, season: str) -> dict[int, tuple[str, ...]]:
        return dict(ROLES) | dict(LISTONE_ONLY) if season == SEASON else {}

    def valuations(self, season: str) -> dict[int, Valuation]:
        return {pid: Valuation(qi=10 + pid, squadra=CLUB) for pid in ROLES}

    def fixtures(self, season: str) -> list[Fixture]:
        return list(FIXTURES) if season == SEASON else []

    def calculated_scores(self, league_id: int) -> list[float]:
        return []

    def latest_sentiment(self, player_ids: Collection[int]) -> dict[int, SentimentRow]:
        return {pid: row for pid, row in self.sentiment.items() if pid in set(player_ids)}


def _inputs(**over: object) -> LineupInputs:
    fields: dict[str, object] = {
        "roster_ids": list(ROLES),
        "roles_by_id": {pid: list(codes) for pid, codes in ROLES.items()},
        "fvmma_by_id": dict(INDEXCOMPARE),
        "modules": ["343"],
        "competition": 311681,
        "mday": 7,
        "cmday": 9,
        "tid": 10000003,
        "bench_size": 2,
    }
    return LineupInputs(**{**fields, **over})  # type: ignore[arg-type]


def _reading(player_id: int, **over: float) -> SentimentRow:
    values = {"disponibilita": 1.0, "titolarita": 1.0, "confidenza": 1.0} | over
    return SentimentRow(
        player_id=str(player_id), nome=f"p{player_id}", data_run=AS_OF.isoformat(),
        sentiment=0.0, mercato=0.0, forma=0.0, rigorista=0.0, piazzati=0.0,
        ruolo_campo="", ruoli_mantra="", deriva_ruolo=0.0, **values,
    )


#: A small budget, because the default tier's rule is N <= 500 and the chain's real one is
#: 20,000. It changes the draws and nothing else — every assertion below is about what the
#: projection *read* and how it valued it, not about a Monte Carlo number.
SMALL = Budget(k=3, lambdas=(0.0,), node_budget=200, m=2, work=8_000)


def _plan(history: FakeHistory | None = None, **over: object) -> ProjectionOutcome:
    budget = over.pop("budget", SMALL)
    return plan_projection(
        _inputs(**over), history or FakeHistory(), as_of=AS_OF, rules=RULES,
        budget=budget,  # type: ignore[arg-type]
    )


class TestTheModelChoosesTheXI:
    def test_the_projection_starts_the_man_the_platform_s_value_benches(self) -> None:
        outcome = _plan()

        assert GOOD in outcome.plans[0].starts
        assert FLASHY not in outcome.plans[0].starts

    def test_a_line_for_every_roster_player_best_value_first(self) -> None:
        outcome = _plan()

        assert [line.player_id for line in outcome.lines][:1] != []
        assert {line.player_id for line in outcome.lines} == set(ROLES)
        values = [line.value for line in outcome.lines]
        assert values == sorted(values, reverse=True)

    def test_the_regular_projects_higher_and_plays_more_often(self) -> None:
        lines = {line.player_id: line for line in _plan().lines}

        assert lines[GOOD].mu > lines[FLASHY].mu
        assert lines[GOOD].p > lines[FLASHY].p
        assert lines[GOOD].sigma_tilde > 0
        assert lines[GOOD].value > lines[FLASHY].value

    def test_the_value_is_the_sub_aware_term(self) -> None:
        outcome = _plan()
        lines = {line.player_id: line for line in outcome.lines}

        assert lines[GOOD].value == pytest.approx(
            lines[GOOD].p * lines[GOOD].mu + (1 - lines[GOOD].p) * outcome.replacement
        )

    def test_a_fresh_injury_prices_him_at_the_replacement_and_not_at_his_own_level(
        self,
    ) -> None:
        """What `p = 0` means under A17(3) with one replacement level: his slot is worth
        whatever the man who comes on for him is worth — so he is *not* benched unless his
        μ falls below `v`. That is the surrogate's known limit (one `v` for every slot, and
        no substitution budget); T22's per-slot `v_s` and T28's sub engine are what fix it.
        The operator sees `p` in the table meanwhile."""
        healthy = {line.player_id: line for line in _plan().lines}
        injured = plan_projection(
            _inputs(),
            FakeHistory({GOOD: _reading(GOOD, disponibilita=0.0, titolarita=0.0)}),
            as_of=AS_OF,
            rules=RULES,
        )

        line = next(line for line in injured.lines if line.player_id == GOOD)
        assert line.p == 0.0
        assert line.mu == pytest.approx(healthy[GOOD].mu)
        assert line.value == pytest.approx(injured.replacement)
        assert line.value < healthy[GOOD].value

    def test_the_classic_role_comes_from_his_latest_appearance(self) -> None:
        lines = {line.player_id: line for line in _plan().lines}

        assert lines[1].role == "P"
        assert lines[3].role == "D"
        assert lines[GOOD].role == "A"
        assert lines[1].macro == "GK"
        # An `E` fielded at the back since g5: his macro says `C`, his last five say `D`.
        assert lines[MOVED].macro == "MID"
        assert lines[MOVED].role == "D"

    def test_a_player_with_no_appearance_takes_his_macro_role_s_letter(self) -> None:
        bare = FakeHistory()
        bare.rows = [row for row in bare.rows if row.player_id != MOVED]

        lines = {line.player_id: line for line in _plan(bare).lines}

        assert lines[MOVED].role == "C"

    def test_sigma_tilde_is_a_spread_in_fantavoto_not_a_variance(self) -> None:
        """Every player's scores are his own level ±0.5, so the spread is about 0.5 — and
        0.55 squared is 0.30, which is what a missing square root would print."""
        lines = {line.player_id: line for line in _plan().lines}

        assert 0.4 < lines[GOOD].sigma_tilde < 0.9


class TestWhatItReads:
    def test_it_reads_five_seasons_ending_at_the_one_being_played(self) -> None:
        history = FakeHistory()

        outcome = plan_projection(_inputs(), history, as_of=AS_OF, rules=RULES)

        assert outcome.seasons == ("2022/23", "2023/24", "2024/25", "2025/26", "2026/27")
        assert history.asked["seasons"] == outcome.seasons

    def test_it_never_reads_past_the_cutoff(self) -> None:
        """The leak guard: the read itself is bounded, not only the fold."""
        history = FakeHistory()

        plan_projection(_inputs(), history, as_of=AS_OF, rules=RULES)

        assert history.asked["before"] == AS_OF

    def test_the_population_is_the_listone_not_the_roster(self) -> None:
        """A 30-man roster is a thin population to fit a prior on; the listone is the one
        the platform hands out roles from."""
        history = FakeHistory()

        plan_projection(_inputs(), history, as_of=AS_OF, rules=RULES)

        assert set(LISTONE_ONLY) <= set(history.asked["ids"])  # type: ignore[operator]
        assert history.asked["ids"] == sorted({*ROLES, *LISTONE_ONLY})


class TestFreshness:
    def test_without_a_recorded_voti_refresh_it_is_stale_and_says_so(self) -> None:
        outcome = _plan()

        assert not outcome.freshness.fresh
        assert any("voti" in reason for reason in outcome.freshness.reasons)

    def test_a_refreshed_history_reaching_the_previous_giornata_is_fresh(self) -> None:
        outcome = plan_projection(
            _inputs(), FakeHistory(), as_of=AS_OF, rules=RULES, voti_refreshed=True
        )

        assert outcome.freshness.fresh
        # Each fake giornata holds one match, not ten: eight warnings, no fallback.
        assert len(outcome.freshness.warnings) == 8

    def test_a_lineup_without_matchday_coordinates_is_stale(self) -> None:
        outcome = plan_projection(
            _inputs(cmday=0), FakeHistory(), as_of=AS_OF, rules=RULES, voti_refreshed=True
        )

        assert not outcome.freshness.fresh
        assert any("matchday" in reason for reason in outcome.freshness.reasons)


# -- T31: the evaluated plan, the settings, and the fallbacks ---------------------------


class TestTheEvaluatedPlan:
    def test_the_chain_runs_and_its_xi_leads_the_list(self) -> None:
        """`plans[0]` *is* the plan, wherever it came from: the submit path walks this list,
        so the model's answer is the one that would be sent."""
        outcome = _plan()

        assert outcome.chosen is not None
        assert outcome.plans[0].starts == outcome.chosen.starts
        assert outcome.plans[0].module == outcome.chosen.module
        assert outcome.plans[0].bench == outcome.chosen.bench

    def test_the_matchday_coordinates_come_from_the_dto_and_not_from_the_chain(self) -> None:
        """The chain knows nothing about competitions; a head built without them would
        submit `tid=0` and a matchday of 0, which the submit path refuses."""
        outcome = _plan()

        assert (outcome.plans[0].competition, outcome.plans[0].tid) == (311681, 10000003)
        assert (outcome.plans[0].mday, outcome.plans[0].cmday) == (7, 9)

    def test_the_matchers_own_answer_survives_behind_it(self) -> None:
        """A refused schema must still have somewhere to fall to, and the fallback that
        needs no opponent and no draws is the list that was already there."""
        outcome = _plan()

        assert len(outcome.plans) >= 1
        assert len({(p.module, p.starts) for p in outcome.plans}) == len(outcome.plans)

    def test_the_same_inputs_give_the_same_plan_twice(self) -> None:
        """The seed is the coordinates, so an hourly job re-planning the same matchday all
        week produces the same XI — and a shadow report can be recomputed."""
        one, two = _plan(), _plan()

        assert one.chosen is not None and two.chosen is not None
        assert (one.chosen.module, one.chosen.starts, one.chosen.bench) == (
            two.chosen.module, two.chosen.starts, two.chosen.bench
        )
        assert one.chosen.evaluation == two.chosen.evaluation

    def test_another_matchday_is_another_seed(self) -> None:
        """The control. Same roster, same history, a different giornata: the draws move."""
        one, two = _plan(), _plan(cmday=10)

        assert one.chosen is not None and two.chosen is not None
        assert one.chosen.evaluation.fantapunti != two.chosen.evaluation.fantapunti

    def test_the_default_tier_takes_at_most_five_hundred_draws(self) -> None:
        """The suite's own rule. The chain's real budget is 20,000 and this file passes a
        small one, so a test that quietly reverted to the default would be measurable here
        before it was measurable in the wall clock."""
        outcome = _plan()

        assert outcome.chosen is not None
        assert outcome.chosen.draws <= 500


class TestTheOpponentDegrades:
    def test_too_few_calculated_rounds_is_a_named_reason_and_not_a_crash(self) -> None:
        """A KDE over four scores is a guess with a probability attached; the plan is still
        worth having without one and says which."""
        outcome = _plan()

        assert outcome.chosen is not None
        assert outcome.chosen.objective == "fantapunti"
        assert outcome.opponent.startswith("none (")
        assert outcome.chosen.evaluation.points is None

    def test_enough_rounds_gives_a_league(self) -> None:
        class WithScores(FakeHistory):
            def calculated_scores(self, league_id: int) -> list[float]:
                return [60.0, 64.0, 66.0, 68.0, 70.0, 72.0, 74.0, 80.0, 55.0, 90.0]

        outcome = _plan(WithScores())

        assert outcome.chosen is not None
        assert outcome.chosen.objective == "points"
        assert outcome.chosen.evaluation.points is not None
        assert outcome.opponent == "10 calculated round(s)"


class TestTheSubMode:
    def test_an_unset_mode_is_assumed_and_recorded_as_assumed(self) -> None:
        """AD4: the three modes field different XIs and Open Question 1 is still open, so
        an unset mode is *assumed* basic and never silently defaulted."""
        outcome = _plan()

        assert (outcome.sub_mode, outcome.sub_mode_assumed) == ("basic", True)

    def test_a_set_mode_is_used_and_not_marked_assumed(self) -> None:
        outcome = plan_projection(
            _inputs(), FakeHistory(), as_of=AS_OF, rules=RULES, budget=SMALL, sub_mode="easy"
        )

        assert (outcome.sub_mode, outcome.sub_mode_assumed) == ("easy", False)


def _after(calls: int) -> object:
    """A `should_stop` that fires after `calls` consultations — a clock without a clock."""
    seen: list[int] = []

    def stop() -> bool:
        seen.append(1)
        return len(seen) > calls

    return stop


class TestTheFallbacks:
    """What the chain contains, and what it deliberately does not.

    `plan_lineups` runs **before** the chain, so a roster that fields no module or cannot
    fill a bench raises there and has nowhere to fall to — that refusal is the right answer
    and is not caught. What the chain adds on top is optional, and every way it can fail is
    a fallback to the matcher's own XI.
    """

    def test_a_classic_lega_never_reaches_the_chain(self) -> None:
        """The substitution engine reads the 11 Mantra schemi; a Classic lega has one role
        per player and a different legality, so the chain refuses it by name rather than
        running it through a matcher answering a question nobody asked.

        Asserted on `_chosen`'s guard itself: a Classic roster does not survive `_targets`
        either — `macro_role` refuses a `P` on the Mantra scale — so a whole-projection test
        would prove the *earlier* refusal and read as if it had proved this one.
        """
        outcome = _chosen(
            _inputs(fmt="classic"),
            roster=[], targets={}, projections={}, p={}, values={}, history_rows=[],
            rules=RULES, sub_mode="basic", opponent=None, league_id=1, max_subs=None,
            budget=SMALL, should_stop=None,
        )

        assert outcome == (None, "classic: the evaluated chain is Mantra-only")

    def test_a_history_too_thin_for_the_copula_falls_back_rather_than_raising(self) -> None:
        """An hourly job that raised here would field nothing at all rather than the XI the
        matcher would have sent."""

        class NoHistory(FakeHistory):
            def appearances(
                self, player_ids: Collection[int], *, seasons: Collection[str], before: date
            ) -> list[HistoryAppearance]:
                return []

        with pytest.raises(ValueError):
            _plan(NoHistory())

    def test_a_roster_that_fields_nothing_raises_rather_than_falling_back(self) -> None:
        """Not a fallback: there is no XI to fall back *to*, and a caller handed an empty
        plan would POST nothing while reporting success."""
        with pytest.raises(NoFieldableModule):
            _plan(modules=[])

    def test_a_bench_that_cannot_be_filled_raises_rather_than_falling_back(self) -> None:
        with pytest.raises(BenchIncomplete):
            _plan(bench_size=99)

    def test_an_aborted_chain_falls_back_rather_than_submitting_a_half_search(self) -> None:
        """SPEC A17(6): the wall clock is a hard abort, **and a hard abort is a fallback**.

        `choose_plan` is right to return a plan rather than raise — a pure chain that threw
        would have no answer to give — but a half-searched plan depends on how loaded the
        machine was, so submitting one would mean the same lega on the same matchday
        fielding two different XIs on two different evenings. The application drops it and
        `plans[0]` goes back to the matcher's answer, which needs no draws and is the same
        everywhere.
        """
        aborted = plan_projection(
            _inputs(), FakeHistory(), as_of=AS_OF, rules=RULES, budget=SMALL,
            should_stop=_after(1),
        )
        clean = _plan()

        assert aborted.chosen is None
        assert aborted.fallback.startswith("aborted: ")
        assert clean.chosen is not None
        # This board fields one module, so the aborted XI and the chain's coincide and
        # asserting on `starts` alone would prove nothing. The **bench** is where the
        # search shows, and the aborted head does not carry the one it half-searched.
        head = (aborted.plans[0].module, aborted.plans[0].starts, aborted.plans[0].bench)
        searched = (clean.chosen.module, clean.chosen.starts, clean.chosen.bench)
        assert head != searched


class TestTheSubstitutionCap:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"subst": {"ssnum": 5}}, 5),
            ({"subst": {"ssnum": "5"}}, 5),
            ({"subst": {"ssnum": 0}}, None),
            ({"subst": {"ssnum": None}}, None),
            ({"subst": {}}, None),
            ({}, None),
            ({"subst": "nonsense"}, None),
        ],
    )
    def test_a_missing_or_zero_cap_reads_as_uncapped(
        self, payload: dict[str, object], expected: int | None
    ) -> None:
        """An engine told it may make no substitutions fields a man short every week, which
        is a worse wrong answer than one that makes a substitution too many."""
        assert substitution_cap(payload) == expected

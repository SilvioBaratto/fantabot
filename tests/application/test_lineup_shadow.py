"""T39: grading what was fielded, and what the shadow would have fielded. No database.

Phase 7's evidence, and the three claims a shadow matchday has to support:

* **our arithmetic agrees with the platform's.** A disagreement over 0.01 means the model
  is being graded by a scorer that does not match the one paying out, and it is a finding
  rather than a rounding;
* **the shadow would have scored X** — the same recompute over the shadow's own ids, which
  is what "the projection would have done better" has to mean;
* **nobody took a malus** (A18), read off the platform's own flag.

Two rules run through all of it. **Ids, never names**: a record carries both, and grading on
names grades the wrong player the first time two share one. And **a record that cannot be
graded says so**, because counting it as a zero drags the average toward whatever the empty
case scores.

The recompute is `domain/lineup/backtest.field` — the same function Gate 1 replays with, so
the two cannot disagree about what an XI scored.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from fantabot.adapters.files.lineup_runs import LineupRun, LineupShadow
from fantabot.application.lineup_shadow import (
    TOLERANCE,
    Graded,
    Ungradable,
    grade_run,
    latest_per_matchday,
    report,
)
from fantabot.domain.lineup.scoring import ScoringRules

RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=1.0,
    decisive_goal=1.0, threshold=66.0, steps=(6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0),
)
STARTS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
BENCH = (1, 2, 3)
ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}),
    1: frozenset({"POR"}), 2: frozenset({"A"}), 3: frozenset({"DC"}),
}


def _run(**over: object) -> LineupRun:
    fields: dict[str, object] = {
        "at": "2026-09-20T18:05:00+02:00",
        "league": 4103937,
        "scheduled": True,
        "status": "submitted",
        "module": "343",
        "matchday": 4,
        "serie_a_matchday": 6,
        "starters": tuple(f"p{i}" for i in STARTS),
        "bench": tuple(f"p{i}" for i in BENCH),
        "starter_ids": STARTS,
        "bench_ids": BENCH,
        "competition": 311681,
        "tid": 10000003,
        "model": "indexcompare",
    }
    fields.update(over)
    return LineupRun(**fields)  # type: ignore[arg-type]


def _shadow(**over: object) -> LineupShadow:
    fields: dict[str, object] = {
        "model": "projection",
        "module": "343",
        "starter_ids": (2, 10, 20, 30, 40, 50, 60, 70, 80, 90, 0),
        "bench_ids": (1, 3, 100),
        "starters": ("a",) * 11,
        "bench": ("b",) * 3,
        "e_pts": 1.1, "p_wdl": (0.3, 0.3, 0.4), "e_fp": 70.0, "sd": 6.0,
    }
    fields.update(over)
    return LineupShadow(**fields)  # type: ignore[arg-type]


def _votes(value: float = 6.0, *, absent: Sequence[int] = ()) -> dict[int, float]:
    return {pid: value for pid in (*STARTS, *BENCH) if pid not in absent}


def _grade(run: LineupRun, **over: object) -> Graded | Ungradable:
    kwargs: dict[str, object] = {
        "votes": _votes(), "roles": ROLES, "rules": RULES, "sub_mode": "basic",
        "modules": ("343",), "platform": None,
    }
    kwargs.update(over)
    return grade_run(run, **kwargs)  # type: ignore[arg-type]


class TestWhichRecordIsGraded:
    def test_the_last_run_of_a_matchday_wins(self) -> None:
        """An hourly job re-submits while the round is open, so the newest record is the XI
        that was actually in play. Grading the first would grade a lineup that was replaced."""
        early = _run(at="2026-09-20T09:00:00+02:00", module="343")
        late = _run(at="2026-09-20T18:00:00+02:00", module="352")

        picked = latest_per_matchday([early, late], league_id=4103937)

        assert [r.module for r in picked] == ["352"]

    def test_matchdays_come_out_in_order(self) -> None:
        picked = latest_per_matchday(
            [_run(matchday=6), _run(matchday=4), _run(matchday=5)], league_id=4103937
        )

        assert [r.matchday for r in picked] == [4, 5, 6]

    def test_another_lega_is_not_graded(self) -> None:
        assert latest_per_matchday([_run(league=3584692)], league_id=4103937) == []

    @pytest.mark.parametrize("status", ["skipped", "failed"])
    def test_a_run_that_fielded_nothing_is_not_graded(self, status: str) -> None:
        assert latest_per_matchday([_run(status=status)], league_id=4103937) == []

    def test_an_unconfirmed_run_is_graded(self) -> None:
        """It reached the platform and could not be read back. The caveat is about the
        *evidence*, not the XI — the lineup is there and it scored something."""
        picked = latest_per_matchday([_run(status="unconfirmed")], league_id=4103937)

        assert len(picked) == 1


class TestTheRecompute:
    def test_a_full_eleven_is_the_eleven_summed(self) -> None:
        graded = _grade(_run())

        assert isinstance(graded, Graded)
        assert graded.ours.fantapunti == pytest.approx(66.0)

    def test_it_substitutes_on_the_real_absences(self) -> None:
        """The engine runs on who took a vote, not on what was submitted — and a
        substitute's score is the one that counts."""
        graded = _grade(_run(), votes=_votes(absent=(80,)) | {2: 9.0})

        assert isinstance(graded, Graded)
        assert graded.ours.entered == (2,)
        assert graded.ours.fantapunti == pytest.approx(10 * 6.0 + 9.0)

    def test_it_reads_ids_and_not_names(self) -> None:
        """A record carries both. Grading on names grades the wrong player the first time
        two share one — which is why record v2 carries ids at all."""
        graded = _grade(_run(starters=("wrong",) * 11, bench=("wrong",) * 3))

        assert isinstance(graded, Graded)
        assert graded.ours.fantapunti == pytest.approx(66.0)

    def test_the_shadow_is_recomputed_over_its_own_ids(self) -> None:
        graded = _grade(_run(shadow=_shadow()), votes=_votes() | {2: 12.0, 100: 1.0})

        assert isinstance(graded, Graded)
        assert graded.shadow is not None
        assert graded.shadow.fantapunti == pytest.approx(graded.ours.fantapunti + 11.0)
        assert graded.delta == pytest.approx(11.0)

    def test_without_a_shadow_there_is_no_delta(self) -> None:
        graded = _grade(_run())

        assert isinstance(graded, Graded)
        assert (graded.shadow, graded.delta) == (None, None)


class TestAgreementWithThePlatform:
    def test_a_matching_recompute_agrees(self) -> None:
        graded = _grade(_run(), platform=66.0)

        assert isinstance(graded, Graded) and graded.agrees

    def test_a_hundredth_is_still_agreement(self) -> None:
        graded = _grade(_run(), platform=66.0 + TOLERANCE)

        assert isinstance(graded, Graded) and graded.agrees

    def test_more_than_a_hundredth_is_a_finding(self) -> None:
        """Not a rounding: the model would be graded by a scorer that does not match the
        one paying out, and nothing downstream is worth reading until it is explained."""
        graded = _grade(_run(), platform=66.0 + 0.011)

        assert isinstance(graded, Graded) and not graded.agrees

    def test_an_uncalculated_round_is_not_a_disagreement(self) -> None:
        """`None` is "the platform has not said", which a 0.0 would turn into "it said
        nothing scored"."""
        graded = _grade(_run(), platform=None)

        assert isinstance(graded, Graded) and graded.agrees


class TestWhatCannotBeGraded:
    def test_a_v1_record_without_ids_is_ungradable(self) -> None:
        """Counting it as a zero would drag the average toward whatever the empty case
        scores."""
        outcome = _grade(_run(starter_ids=(), bench_ids=()))

        assert isinstance(outcome, Ungradable)
        assert "starter ids" in outcome.reason

    def test_a_player_with_no_role_is_ungradable_and_named(self) -> None:
        outcome = _grade(_run(starter_ids=(999, *STARTS[1:])))

        assert isinstance(outcome, Ungradable)
        assert "999" in outcome.reason

    def test_a_shadow_whose_ids_have_no_roles_is_dropped_and_the_run_still_grades(
        self,
    ) -> None:
        """The sent XI is the thing being graded; a shadow that cannot be recomputed costs
        the comparison, not the record."""
        graded = _grade(_run(shadow=_shadow(starter_ids=(999, *STARTS[1:]))))

        assert isinstance(graded, Graded)
        assert graded.shadow is None
        assert graded.ours.fantapunti == pytest.approx(66.0)


class TestTheMalusCheck:
    def test_a_clean_matchday_flags_nothing(self) -> None:
        graded = _grade(_run())

        assert isinstance(graded, Graded) and graded.malus_starters == ()

    def test_a_flagged_starter_is_carried_and_surfaces_in_the_report(self) -> None:
        """A18: this must be zero. Read off the platform's own `m` flag, never inferred
        from a score that came out a point short."""
        graded = _grade(_run(), malus_starters=(20,))

        assert isinstance(graded, Graded) and graded.malus_starters == (20,)
        outcome = report([_run()], league_id=4103937, sub_mode="basic", grade=lambda _r: graded)

        assert outcome.maluses == (graded,)
        assert any("MALUS on [20]" in line for line in outcome.lines())


class TestTheReport:
    def test_it_separates_the_graded_from_the_ungradable(self) -> None:
        runs = [_run(matchday=4), _run(matchday=5, starter_ids=(), bench_ids=())]

        outcome = report(
            runs, league_id=4103937, sub_mode="easy",
            grade=lambda run: _grade(run, platform=66.0),
        )

        assert len(outcome.graded) == 1
        assert len(outcome.ungradable) == 1
        assert "ungradable" in "\n".join(outcome.lines())

    def test_a_mismatch_is_counted_and_explained(self) -> None:
        outcome = report(
            [_run()], league_id=4103937, sub_mode="easy",
            grade=lambda run: _grade(run, platform=99.0),
        )

        assert len(outcome.mismatches) == 1
        text = "\n".join(outcome.lines())
        assert "MISMATCH" in text
        assert "does not match the one paying out" in text

    def test_the_report_names_the_mode_it_graded_under(self) -> None:
        """The three field different XIs, so a grade under the wrong one grades a game
        nobody played."""
        outcome = report([], league_id=4103937, sub_mode="easy", grade=lambda _r: _grade(_run()))

        assert "sub mode easy" in outcome.lines()[0]

    def test_a_hand_computed_matchday_reproduces(self) -> None:
        """The whole chain on numbers a reader can check: eleven men at 6.5 is 71.5, which
        is one goal on a ladder whose first rung is 66 and whose second is 72."""
        graded = _grade(_run(), votes=_votes(6.5), platform=71.5)

        assert isinstance(graded, Graded)
        assert graded.ours.fantapunti == pytest.approx(71.5)
        assert graded.ours.goals == 1
        assert graded.agrees


class TestTheDecisiveGoalGap:
    """Our recompute is **knowably** short, and by exactly one thing.

    The platform pays `bmdg` — +1 once to a scorer whose goal decided the match — and
    `match_grain` has no column for it. T12 measured it: 147 of 153 players exact in round 1,
    5 short by `bmdg`, 1 by a malus the engine now reproduces. So a gap that is a small
    non-negative multiple of `bmdg` is explained, and anything else is a finding.

    Without this, every matchday in which one of our eleven scored a decisive goal would
    report `MISMATCH` — which is how a check stops being read.
    """

    def test_one_decisive_goal_is_explained_and_counted(self) -> None:
        graded = _grade(_run(), platform=67.0)

        assert isinstance(graded, Graded)
        assert graded.agrees
        assert graded.decisive_goals == 1

    def test_three_are_explained(self) -> None:
        graded = _grade(_run(), platform=69.0)

        assert isinstance(graded, Graded) and graded.agrees
        assert graded.decisive_goals == 3

    def test_an_exact_match_counts_none(self) -> None:
        graded = _grade(_run(), platform=66.0)

        assert isinstance(graded, Graded) and graded.agrees
        assert graded.decisive_goals == 0

    def test_a_gap_that_is_not_a_multiple_is_a_finding(self) -> None:
        """1.5 is not a decisive goal and never will be. The allowance is for one known
        omission, not a widened tolerance."""
        graded = _grade(_run(), platform=67.5)

        assert isinstance(graded, Graded)
        assert not graded.agrees
        assert graded.decisive_goals is None

    def test_paying_us_less_than_we_computed_is_always_a_finding(self) -> None:
        """`bmdg` only ever adds. A platform total *below* ours cannot be explained by it,
        and a signed allowance would have hidden exactly that."""
        graded = _grade(_run(), platform=65.0)

        assert isinstance(graded, Graded) and not graded.agrees

    def test_an_implausible_number_of_them_is_a_finding(self) -> None:
        """Seven decisive goals in one XI is a real disagreement wearing a plausible shape."""
        graded = _grade(_run(), platform=73.0)

        assert isinstance(graded, Graded) and not graded.agrees

    def test_the_report_says_when_a_gap_was_explained(self) -> None:
        graded = _grade(_run(), platform=68.0)
        assert isinstance(graded, Graded)

        outcome = report([_run()], league_id=4103937, sub_mode="basic", grade=lambda _r: graded)

        assert any("+2 bmdg" in line for line in outcome.lines())
        assert outcome.mismatches == ()

    def test_the_ladder_reads_our_number_and_not_the_platforms(self) -> None:
        """The allowance explains a *disagreement*; it does not change what we computed, and
        the goals we report are the ones our own total makes."""
        graded = _grade(_run(), platform=72.0)

        assert isinstance(graded, Graded)
        assert graded.ours.fantapunti == pytest.approx(66.0)
        assert graded.ours.goals == 1


class TestTwoCompetitions:
    def test_a_second_competitions_matchday_does_not_replace_the_first(self) -> None:
        """`LineupRun.matchday` is the *competition's* own numbering and a lega can hold
        more than one. Keyed on the matchday alone, the report came out a row short with
        nothing saying so."""
        coppa = _run(competition=999999, matchday=4, module="352")

        picked = latest_per_matchday([_run(), coppa], league_id=4103937)

        assert len(picked) == 2
        assert {r.competition for r in picked} == {311681, 999999}


class TestARecordThatDoesNotNameItsCompetition:
    """A v1 line never carried one, and on the operator's own log 27 of 52 gradable records
    are that shape. Keyed as a competition of their own they made one matchday appear twice,
    once as itself and once as a matchday nobody could identify."""

    def test_it_joins_a_named_competitions_matchday(self) -> None:
        old = _run(at="2026-09-22T08:00:00+02:00", competition=None, starter_ids=(), bench_ids=())
        new = _run(at="2026-09-23T09:00:00+02:00", competition=311681)

        picked = latest_per_matchday([old, new], league_id=4103937)

        assert len(picked) == 1
        assert picked[0].competition == 311681

    def test_it_stands_alone_when_no_named_record_claims_that_matchday(self) -> None:
        """Dropping it would lose the only record of a matchday the v1 writer recorded."""
        old = _run(at="2026-09-22T08:00:00+02:00", competition=None, matchday=3)

        picked = latest_per_matchday([old, _run(matchday=4)], league_id=4103937)

        assert [(r.matchday, r.competition) for r in picked] == [(3, None), (4, 311681)]

    def test_the_newest_unnamed_record_of_a_matchday_wins(self) -> None:
        early = _run(at="2026-09-22T08:00:00+02:00", competition=None, module="343")
        late = _run(at="2026-09-22T18:00:00+02:00", competition=None, module="352")

        picked = latest_per_matchday([early, late], league_id=4103937)

        assert [r.module for r in picked] == ["352"]

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
from fantabot.application.lineup_projection import ProjectionOutcome, plan_projection
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


def _plan(history: FakeHistory | None = None, **over: object) -> ProjectionOutcome:
    return plan_projection(
        _inputs(**over), history or FakeHistory(), as_of=AS_OF, rules=RULES
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

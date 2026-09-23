"""Gate 1 against the real corpus, one room and three giornate. Marked `dbdata`.

A smoke test, not a gate run: the point is that every seam the replay crosses — the corpus
read, the id recovery, the season reads, the per-giornata refit, both arms, the table and
the pairing — holds against 614k real rows, which no fake can prove. The gate itself is
`fantabot lineup backtest`, it is an overnight command, and its verdict is the operator's
at CP5.

It is also where the phase's **most important measurement** lives, because it is not a
number any fake could have produced.

⚠ **Gate 1 as SPEC A16 specifies it cannot be run, and the reason is arithmetic.** The
corpus rosters were bought in a 2026/27 auction; A16(2) makes a player eligible only if he
has a `quotazioni` row for the *replayed* season, and earlier seasons simply do not contain
most of a 2026/27 roster. Measured 2026-09-23 over all 148 rosters, the count that can
field eleven plus a bench of:

```
                 eligible count >= 11+b        of those, role-feasible (a real XI + bench)
season     pool   b=1  b=3  b=5  b=8  b=12      b=1  b=3  b=5  b=8  b=12
2023/24     247   116   87   46    7    0        39   36   24    6    0
2024/25     328   148  138  120   74   12        86   85   76   47   12
2025/26     410   148  148  148  131   65       109  109  109   99   56
```

The right-hand block is the one that matters: a count of eligible players is not a lineup,
and a subset of a rosa is usually missing a keeper or a third centre-back. At the lega's
real bench of **12 the sweep season fields nothing at all** and 2024/25 fields 12 of 148, so
a gate run at bench 12 would report a sweep result that cannot have happened.

At bench 3 it is runnable — 36 / 85 / 109 rosters — and that is a **different game**: the
auto-sub engine has fewer men to cover with, which is precisely what the model is being
graded on. **The operator chose 3 on 2026-09-23** (`GATE_BENCH`), and the report names the
limitation it buys: what Gate 1 measures is the model's edge at a three-man bench, and
carrying that to a twelve-man one is an assumption rather than a result.

**And it is what the full run will cost.** This file replays one room over eight giornate in
**141 s** (measured 2026-09-23), which is about 1.4 s a model plan at `BACKTEST_BUDGET`. A
whole Gate 1 at bench 3 is then roughly 109 x 33 x 2 graded plans plus 36 x 33 x 3 swept
ones — about **4 to 5 CPU-hours**, which is an overnight command and not an infeasible one.
That number is the reason the budget is what it is: a live-sized plan takes 192 s, and the
same gate at that budget would take five months.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.lineup_history import (
    BacktestCorpusRepository,
    LineupHistoryRepository,
)
from fantabot.application.lineup_backtest import (
    BACKTEST_BUDGET,
    FIRST_MODEL_GIORNATA,
    GATE_BENCH,
    ReplaySettings,
    read_season,
    replay_season,
)
from fantabot.application.lineup_planner import LineupInputs, plan_lineups
from fantabot.application.reporting import SilentReporter
from fantabot.domain.lineup.backtest_corpus import BacktestRoom, Corpus, admit
from fantabot.domain.lineup.errors import LineupError
from fantabot.domain.lineup.scoring import ScoringRules

pytestmark = [pytest.mark.db, pytest.mark.dbdata]

SEASON = "2025/26"
#: The gate's own bench, chosen by the operator on the measurement below. Imported rather
#: than restated: a smoke test that ran at a different bench from the gate would be proving
#: the seams of a run nobody makes.
BENCH = GATE_BENCH
#: This lega's own, from `settings/calculate` — not re-read here, because a smoke test that
#: needed a token would be a smoke test nobody could run.
RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=1.0,
    decisive_goal=1.0, threshold=66.0, steps=(6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0),
)
MODULES = ("3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442")


def _fieldable(ids: Sequence[int], roles: Mapping[int, Sequence[str]]) -> bool:
    """Whether this subset can actually field a module. **Not a count.**

    A count of eligible players is not a lineup: 22 of a 27-man rosa, measured, had no
    keeper and no back line and fielded none of the eleven schemi. Ranking rooms by count
    picked one where every roster failed, and the smoke test then reported "no paired row"
    for a reason that had nothing to do with the seams it was crossing.
    """
    if len(ids) < 11 + BENCH:
        return False
    try:
        plan_lineups(
            LineupInputs(
                roster_ids=list(ids),
                roles_by_id={pid: list(roles.get(pid, ())) for pid in ids},
                fvmma_by_id=dict.fromkeys(ids, 1.0),
                modules=list(MODULES), competition=0, mday=1, cmday=1, tid=0,
                bench_size=BENCH,
            )
        )
    except LineupError:
        return False
    return True


def _richest_room(corpus: Corpus, roles: Mapping[int, Sequence[str]], eligible: set[int]) -> Corpus:
    """The corpus, narrowed to the one room with the most rosters this season can field."""
    def fieldable(room: BacktestRoom) -> int:
        return sum(
            1
            for roster in room.rosters
            if _fieldable([pid for pid in roster.player_ids if pid in eligible], roles)
        )

    return Corpus((max(corpus.rooms, key=fieldable),), ())


def _settings(**over: object) -> ReplaySettings:
    fields: dict[str, object] = {
        "rules": RULES,
        "sub_mode": "easy",
        "modules": MODULES,
        "bench_size": BENCH,
        "budget": BACKTEST_BUDGET,
        "max_subs": 5,
        "rooms": 1,
        "last": FIRST_MODEL_GIORNATA + 2,
    }
    fields.update(over)
    return ReplaySettings(**fields)  # type: ignore[arg-type]


def test_one_room_and_three_giornate_replay_end_to_end(db_session: Session) -> None:
    """Every seam at once. What it proves is that the pieces join on real ids — which is
    exactly what a fake cannot, since a fake chooses its own."""
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")
    corpus = admit(rooms, sales)
    if not corpus.rooms:
        pytest.fail("the corpus admitted no room; the replay below would be vacuous")
    repo = LineupHistoryRepository(db_session)
    data = read_season(repo, SEASON)
    eligible = set(repo.valuations(SEASON)) & set(repo.roles("2026/27"))

    result = replay_season(
        data, _richest_room(corpus, repo.roles("2026/27"), eligible), _settings(), reporter=SilentReporter()
    )

    assert result.rooms == 1
    assert result.paired, f"no paired row; skipped: {result.skipped}"
    assert {row.giornata for row in result.paired} <= {
        FIRST_MODEL_GIORNATA, FIRST_MODEL_GIORNATA + 1, FIRST_MODEL_GIORNATA + 2
    }


def test_both_arms_field_eleven_real_players(db_session: Session) -> None:
    """The realized scores are sums of real fantavoti, so they land where fantavoti land —
    a replay that silently fielded nobody would read as a very cautious model."""
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")
    repo = LineupHistoryRepository(db_session)
    data = read_season(repo, SEASON)
    eligible = set(repo.valuations(SEASON)) & set(repo.roles("2026/27"))

    result = replay_season(
        data, _richest_room(admit(rooms, sales), repo.roles("2026/27"), eligible), _settings(),
        reporter=SilentReporter(),
    )

    assert result.paired
    for row in result.paired:
        assert 0.0 < row.baseline_fantapunti < 200.0
        assert 0.0 < row.model_fantapunti < 200.0


def test_the_replay_is_the_same_replay_twice(db_session: Session) -> None:
    """On real data, not only on a fake: the seed is the giornata and the roster, so the
    same command twice is the same evidence — which is what a gate has to be."""
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")
    repo = LineupHistoryRepository(db_session)
    data = read_season(repo, SEASON)
    eligible = set(repo.valuations(SEASON)) & set(repo.roles("2026/27"))
    corpus = _richest_room(admit(rooms, sales), repo.roles("2026/27"), eligible)

    one = replay_season(data, corpus, _settings(), reporter=SilentReporter())
    two = replay_season(data, corpus, _settings(), reporter=SilentReporter())

    assert one.paired == two.paired


def test_the_eligibility_rule_is_what_bounds_the_gate(db_session: Session) -> None:
    """The measurement the module docstring turns on, asserted so it cannot rot silently.

    A16(2) makes a player eligible only if he has a `quotazioni` row for the replayed
    season, and the corpus rosters were bought in 2026/27. The gate's own bench of 12 leaves
    the sweep season with **no** fieldable roster — so a run that reported a sweep result at
    bench 12 would be reporting something that cannot have happened.
    """
    rooms, sales = BacktestCorpusRepository(db_session).corpus_rows()
    if not sales:
        pytest.skip("no harvested sales in this database")
    repo = LineupHistoryRepository(db_session)
    roles = repo.roles("2026/27")
    corpus = admit(rooms, sales)

    def fieldable(season: str, bench: int) -> int:
        eligible = set(repo.valuations(season)) & set(roles)
        return sum(
            1
            for room in corpus.rooms
            for roster in room.rosters
            if len([pid for pid in roster.player_ids if pid in eligible]) >= 11 + bench
        )

    assert fieldable("2023/24", 12) == 0
    assert fieldable("2024/25", 12) < 20
    assert fieldable("2025/26", BENCH) == 148

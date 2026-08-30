"""The roster optimizer against the real Mantra listone. Marked ``db``.

The pure tests pin the algorithm on fixtures; this pins the *invariants* on the real 548-man
pool across a range of risk settings — that every roster it returns is a legal, budget-
consistent 30-man. A single hand-run smoke covered one lambda; a live evening will not.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fantabot.asta_engine.legality import fieldable_schemi
from fantabot.asta_engine.optimizer import optimize_roster
from fantabot.asta_engine.plan import PlanInputs, read_plan_inputs
from fantabot.asta_engine.state import AstaState, RosterRules

pytestmark = pytest.mark.db

BUDGET = 500.0
RULES = RosterRules()


def _world(session: Session) -> PlanInputs:
    """The same assembly the three commands use.

    This was a fourth copy of it — the one nobody had counted when the spec said the
    block appeared three times. Sharing it is what makes this test an invariant check on
    *the real thing*, rather than on a parallel construction that could drift away from
    what the commands actually plan with.

    `sentiment=None` keeps the pre-sentiment model these invariants were written against;
    the sentiment path has its own coverage in `tests/test_asta_sentiment_wiring.py` and
    in the golden harness.
    """
    return read_plan_inputs(
        session, season="2026/27", sentiment=None, as_of=None, tilt_k=0.25
    )


@pytest.mark.parametrize("lam", [0.0, 0.5, 2.0])
def test_the_real_listone_always_optimizes_to_a_legal_budget_rosa(db_session: Session, lam: float) -> None:
    world = _world(db_session)
    if not world.pool:
        pytest.skip("no Mantra listone loaded")

    result = optimize_roster(
        AstaState(total_budget=BUDGET), world.pool,
        value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, rules=RULES, lam=lam,
    )
    roster = result.optimal
    by_id = {p.id: p for p in world.pool}

    assert len(roster) == RULES.size
    assert roster.total_cost <= BUDGET
    assert fieldable_schemi([by_id[pid] for pid in roster.player_ids], world.legality)
    goalkeepers = sum(1 for pid in roster.player_ids if "POR" in set(world.roles.get(pid, ())))
    assert goalkeepers == RULES.min_goalkeepers
    assert len(set(roster.player_ids)) == RULES.size  # no duplicates

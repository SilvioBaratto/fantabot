"""The roster optimizer against the real Mantra listone. Marked ``db``.

The pure tests pin the algorithm on fixtures; this pins the *invariants* on the real 548-man
pool across a range of risk settings — that every roster it returns is a legal, budget-
consistent 30-man. A single hand-run smoke covered one lambda; a live evening will not.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fantabot.asta_engine.legality import build_legality, fieldable_schemi, load_compat
from fantabot.asta_engine.optimizer import optimize_roster
from fantabot.asta_engine.prices import expected_prices
from fantabot.asta_engine.report import build_pool, build_value
from fantabot.asta_engine.state import AstaState, RosterRules
from fantabot.db.repositories.reference import ReferenceRepository

pytestmark = pytest.mark.db

BUDGET = 500.0
RULES = RosterRules()


def _world(session: Session):
    quotazioni = ReferenceRepository(session).quotazioni("2026/27", "mantra")
    prices = expected_prices(session)
    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    value = build_value({pid: row.fvm for pid, row in quotazioni.items()}, priced_ids=set(prices))
    roles = {pid: set(row.ruoli_codice) for pid, row in quotazioni.items()}
    return pool, teams, value, prices, roles, build_legality(load_compat())


@pytest.mark.parametrize("lam", [0.0, 0.5, 2.0])
def test_the_real_listone_always_optimizes_to_a_legal_budget_rosa(db_session: Session, lam: float) -> None:
    pool, teams, value, prices, roles, legality = _world(db_session)
    if not pool:
        pytest.skip("no Mantra listone loaded")

    result = optimize_roster(
        AstaState(total_budget=BUDGET), pool,
        value=value, prices=prices, teams=teams, legality=legality, rules=RULES, lam=lam,
    )
    roster = result.optimal
    by_id = {p.id: p for p in pool}

    assert len(roster) == RULES.size
    assert roster.total_cost <= BUDGET
    assert fieldable_schemi([by_id[pid] for pid in roster.player_ids], legality)
    goalkeepers = sum(1 for pid in roster.player_ids if "POR" in roles.get(pid, set()))
    assert goalkeepers == RULES.min_goalkeepers
    assert len(set(roster.player_ids)) == RULES.size  # no duplicates

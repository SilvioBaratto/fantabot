"""The same-team dependence over the recorded seasons. ``db`` + ``dbdata``.

The unit tests prove the copula on worlds built to have exact answers; this proves the fit
lands where SPEC A11 measured it, on the lega's own history: the current listone's
appearances over five seasons, standardized against the projection at H = 180 days (T15),
paired by club and day, with each class's copula rho moment-matched (SPEC A25).
Read-only, inside the rolled-back transaction.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from _paths import FIXTURES
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.lineup_history import LineupHistoryRepository
from fantabot.domain.asta.roles import macro_role
from fantabot.domain.lineup.dependence import fit, output_correlation, residuals
from fantabot.domain.lineup.projection import ProjectionConfig, Target, observations, project
from fantabot.domain.lineup.scoring import ScoringRules

pytestmark = [pytest.mark.db, pytest.mark.dbdata]

AS_OF = date(2026, 9, 22)
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26", "2026/27")
CURRENT = "2026/27"


def test_the_pooled_same_team_correlation_is_small_and_positive(db_session: Session) -> None:
    calculate = json.loads(
        (FIXTURES / "lineup_reconcile" / "calculate_4103937.json").read_text(encoding="utf-8")
    )
    rules = ScoringRules.from_settings(calculate["bnMls"], calculate["step"])
    repo = LineupHistoryRepository(db_session)
    current = repo.valuations(CURRENT)
    players = {
        pid: Target(
            player_id=pid,
            macro=macro_role(";".join(codes), "mantra"),
            qi=current[pid].qi if pid in current else None,
            club=current[pid].squadra if pid in current else None,
        )
        for pid, codes in repo.roles(CURRENT).items()
    }
    history = observations(
        repo.appearances(list(players), seasons=SEASONS, before=AS_OF),
        rules=rules,
        valuations={season: repo.valuations(season) for season in SEASONS},
    )
    projections = project(history, players, as_of=AS_OF, config=ProjectionConfig(180.0))

    dep = fit(residuals(history, projections, players))

    # 126,266 pairs on 2026-09-22; a floor against a read gone thin.
    assert sum(dep.pairs.values()) > 100_000
    # SPEC A11 measured +0.092; 0.104 on 2026-09-22.
    assert 0.05 <= dep.pooled <= 0.15
    # The clean-sheet coupling is the strongest class (A11: +0.217).
    big = [cls for cls, n in dep.pairs.items() if n >= 1_000]
    assert max(big, key=lambda cls: dep.pearson[cls]) == ("DEF", "GK")
    # A25: every class's copula rho reproduces its target through the real, skewed marginals,
    # and has to exceed it to do so.
    for a, b in big:
        assert output_correlation(
            dep.rho[a, b], dep.marginal(a), dep.marginal(b)
        ) == pytest.approx(dep.pearson[a, b], abs=1e-9)
        assert dep.rho[a, b] > dep.pearson[a, b] > 0

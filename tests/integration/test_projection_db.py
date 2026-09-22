"""The projection over the recorded seasons, for the operator's own roster. ``db`` + ``dbdata``.

The unit tests prove the arithmetic on synthetic worlds; this proves it survives the real
one: the current listone's 27k appearances over five seasons, roles from the 2026/27 Mantra
listone, each appearance at its own season's `qi` and club, scored with the lega's own
rules. Read-only, inside the rolled-back transaction.

The roster is literal (SPEC A15): RealEspresso's 30 players as `lineUpInfo` listed them on
2026-09-22 (`tests/golden/lineup/teamLineup_read.json`) — never the latest
`league_team_snapshot`, whose own-team row is NULL.
"""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from _paths import FIXTURES
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.lineup_history import LineupHistoryRepository
from fantabot.domain.asta.roles import macro_role
from fantabot.domain.lineup.projection import ProjectionConfig, Target, observations, project
from fantabot.domain.lineup.scoring import ScoringRules

pytestmark = [pytest.mark.db, pytest.mark.dbdata]

AS_OF = date(2026, 9, 22)
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26", "2026/27")
CURRENT = "2026/27"

#: RealEspresso (18780035), 2026-09-22.
ROSTER = (
    1850, 2155, 2194, 2788, 4137, 4179, 4360, 4436, 4459, 4947, 4998, 5319, 5504, 5620, 5678,
    5680, 5750, 6052, 6482, 6534, 6549, 6875, 6898, 7126, 7181, 7198, 7274, 7564, 7600, 7612,
)


def test_every_roster_player_gets_a_finite_projection(db_session: Session) -> None:
    assert len(set(ROSTER)) >= 23, "a Mantra rosa is at least 23 players (minrl)"
    calculate = json.loads(
        (FIXTURES / "lineup_reconcile" / "calculate_4103937.json").read_text(encoding="utf-8")
    )
    rules = ScoringRules.from_settings(calculate["bnMls"], calculate["step"])
    repo = LineupHistoryRepository(db_session)

    roles = repo.roles(CURRENT)
    current = repo.valuations(CURRENT)
    players = {
        pid: Target(
            player_id=pid,
            macro=macro_role(";".join(codes), "mantra"),
            qi=current[pid].qi if pid in current else None,
            club=current[pid].squadra if pid in current else None,
        )
        for pid, codes in roles.items()
    }
    history = observations(
        repo.appearances(list(players), seasons=SEASONS, before=AS_OF),
        rules=rules,
        valuations={season: repo.valuations(season) for season in SEASONS},
    )

    result = project(history, players, as_of=AS_OF, config=ProjectionConfig(180.0))

    # 27,110 on 2026-09-22: the history of the players in the current listone, which is
    # the population a role can be given to. A floor, against a read gone thin.
    assert len(history) > 20_000
    missing = [pid for pid in ROSTER if pid not in result]
    assert missing == [], f"roster players with no 2026/27 Mantra role: {missing}"
    bad = [
        (pid, result[pid].mu, result[pid].sigma_tilde2)
        for pid in ROSTER
        if not math.isfinite(result[pid].mu) or not result[pid].sigma_tilde2 > 0
    ]
    assert bad == []
    # SPEC A23: with ȳ's variance taken as s²/Σw, this was exactly 0 at H = 180, and every
    # μ was its prior. 0.041 on 2026-09-22.
    assert result[ROSTER[0]].tau2 > 0.01

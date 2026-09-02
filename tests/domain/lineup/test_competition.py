"""`resolve_competition` — pick the competition to act on, or refuse when it is not unique.

Cron passes no flag, so the competition is resolved from `/league/competitions`: the one that
is not deleted and includes our team. Zero or several is not something to guess at — it
raises, and the operator passes `--competition`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.competition import resolve_competition
from fantabot.domain.lineup.errors import CompetitionAmbiguous, NoCompetition

TID = 10000003


def _c(cid: int, *, tids: list[int], deleted: bool = False) -> dict[str, object]:
    return {"id": cid, "tmids": tids, "del": deleted, "sDay": 3, "eDay": 38}


def test_the_single_active_competition_with_our_team_is_chosen() -> None:
    comps = [_c(311681, tids=[TID, 1, 2]), _c(177318, tids=[1, 2], deleted=False)]

    assert resolve_competition(comps, tid=TID) == 311681


def test_a_deleted_competition_is_ignored() -> None:
    comps = [_c(311681, tids=[TID], deleted=True), _c(177318, tids=[TID])]

    assert resolve_competition(comps, tid=TID) == 177318


def test_no_competition_with_our_team_raises() -> None:
    with pytest.raises(NoCompetition):
        resolve_competition([_c(1, tids=[7, 8])], tid=TID)


def test_several_candidates_are_ambiguous_and_named() -> None:
    comps = [_c(311681, tids=[TID]), _c(177318, tids=[TID])]

    with pytest.raises(CompetitionAmbiguous, match="311681"):
        resolve_competition(comps, tid=TID)

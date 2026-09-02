"""`value.score` — each player's ranking signal, the value on `RosterPlayer.fvmma`. Pure."""

from __future__ import annotations

from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.value import score

ROSTER = [
    RosterPlayer(id=6482, roles=frozenset({"POR"}), fvmma=6.0),
    RosterPlayer(id=4179, roles=frozenset({"W", "A"}), fvmma=40.0),
]


def test_score_is_the_per_player_value() -> None:
    assert score(ROSTER) == {6482: 6.0, 4179: 40.0}


def test_an_empty_roster_scores_to_an_empty_map() -> None:
    assert score([]) == {}

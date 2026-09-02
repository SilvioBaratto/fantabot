"""`value.score` — each player's ranking signal, `fvmma x sentiment`. Pure.

The multiplier is `domain/asta/sentiment.effect_by_id`, computed by the caller with an
`as_of` it passes in, so this module reads no clock. With no effect the score is the raw
`fvmma` — the `--no-sentiment` ablation the auction side also carries.
"""

from __future__ import annotations

from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.value import score

ROSTER = [
    RosterPlayer(id=6482, roles=frozenset({"POR"}), fvmma=6.0),
    RosterPlayer(id=4179, roles=frozenset({"W", "A"}), fvmma=40.0),
]


def test_score_is_fvmma_times_the_effect_multiplier() -> None:
    scores = score(ROSTER, effect={6482: 1.0, 4179: 1.5})

    assert scores == {6482: 6.0, 4179: 60.0}


def test_no_effect_reproduces_the_raw_fvmma() -> None:
    assert score(ROSTER) == {6482: 6.0, 4179: 40.0}


def test_a_player_absent_from_the_effect_map_gets_a_neutral_multiplier() -> None:
    scores = score(ROSTER, effect={4179: 2.0})

    assert scores[6482] == 6.0  # neutral 1.0
    assert scores[4179] == 80.0

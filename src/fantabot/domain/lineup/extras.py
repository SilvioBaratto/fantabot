"""Captain, vice and the switch: what a submitted lineup carries besides the XI and bench. Pure.

Both are per-lega options, read from `settings/lineup`, and both encodings were captured live
2026-09-23 on lega 3677376 (Classic):

* `capt` is `[captain, vice]`, player ids, both starters. `lcap` 2 had a captain set; `lcap` 3
  (lega 2761635) has none, by the operator's own account. Any other `lcap` is unmeasured and
  sends no captain rather than a guessed one.
* `swtcA` is a starter and `swtcB` the bench player who replaces him first; the capture paired
  two defenders (Delprato -> Vojvoda). `lswi` 2 and 3 both had a switch; 1 is on the two
  leghe with no competition at all. Same rule: anything unmeasured sends no switch.

The choice itself runs on `predict.Prediction`: the captain is the starter with the highest
expected fantavoto; the switch protects the starter most likely *not* to play with the best
same-role reserve, which keeps the module legal whichever of them ends up playing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup.defence import VOTE_SD
from fantabot.domain.lineup.predict import Prediction
from fantabot.domain.lineup.rules import BandedModifier

#: `lcap` -> how many ids `capt` carries. Measured values only.
CAPTAIN_SLOTS_BY_LCAP: Mapping[int, int] = {2: 2, 3: 0}
#: `lswi` values on which the platform accepts a switch. Measured values only.
SWITCH_ENABLED_LSWI: frozenset[int] = frozenset({2, 3})
#: `lswi` values whose switch may cross roles (a midfielder on for a defender, changing the
#: module). 3 is lega 3677376, where the operator can pick a defender or a midfielder; 2 is
#: 2761635, where he cannot.
SWITCH_CROSS_ROLE_LSWI: frozenset[int] = frozenset({3})


def captain_slots(lcap: object) -> int:
    """How many captain ids to send for this `lcap`; 0 when unset or unmeasured."""
    try:
        return CAPTAIN_SLOTS_BY_LCAP.get(int(lcap), 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def switch_enabled(lswi: object) -> bool:
    """Whether this `lswi` takes a switch; False when unset or unmeasured."""
    try:
        return int(lswi) in SWITCH_ENABLED_LSWI  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return False


def switch_cross_role(lswi: object) -> bool:
    """Whether this `lswi`'s switch may bring on a player of another role."""
    try:
        return int(lswi) in SWITCH_CROSS_ROLE_LSWI  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return False


def choose_defence_switch(
    starts: Sequence[int],
    bench: Sequence[int],
    roles: Mapping[int, str],
    predictions: Mapping[int, Prediction],
) -> tuple[int, int] | None:
    """`(defender, midfielder)`: the starting defender least likely to play, covered by the
    best-expected bench midfielder — the fallback that makes a risky back four worth it on a
    lega whose switch crosses roles. None when either side is missing."""
    defenders = [pid for pid in starts if roles.get(pid) == "D" and pid in predictions]
    midfielders = [
        pid for pid in bench
        if roles.get(pid) == "C" and pid in predictions and predictions[pid].expected > 0
    ]
    if not defenders or not midfielders:
        return None
    riskiest = min(defenders, key=lambda pid: (predictions[pid].p_play, predictions[pid].expected))
    return riskiest, max(midfielders, key=lambda pid: predictions[pid].expected)


def captain_value(
    captain: int,
    vice: int | None,
    predictions: Mapping[int, Prediction],
    modifier: BandedModifier,
    *,
    vote_sd: float = VOTE_SD,
) -> float:
    """E[captain bonus] for this pair: the captain's table value if he plays, else the
    vice's if *he* plays, else nothing."""
    c = predictions[captain]
    value = c.p_play * modifier.expected(c.vote_if_plays, vote_sd)
    if vice is not None and vice in predictions:
        v = predictions[vice]
        value += (1.0 - c.p_play) * v.p_play * modifier.expected(v.vote_if_plays, vote_sd)
    return value


def choose_captains(
    starts: Sequence[int],
    predictions: Mapping[int, Prediction],
    *,
    slots: int,
    modifier: BandedModifier | None = None,
) -> tuple[int, ...]:
    """The captain (and vice when `slots` is 2), best first.

    With a captain `modifier` (3677376's `smodcp`: the captain's *vote* earns -1.5 … +1.5) the
    pair maximising `captain_value` is taken — a sure 6.5 beats a doubtful 7, and the vice
    only counts when the captain misses. Without one, the starters with the highest expected
    fantavoto: ranked on `expected` (which already carries `p_play`), so a doubtful star loses
    the band to a certain starter. Ties keep the XI order (stable sort).
    """
    if slots <= 0:
        return ()
    candidates = [pid for pid in starts if pid in predictions]
    if modifier is not None and candidates:
        if slots == 1:
            best = max(candidates, key=lambda c: captain_value(c, None, predictions, modifier))
            return (best,)
        pairs = [(c, v) for c in candidates for v in candidates if v != c]
        if pairs:
            return max(pairs, key=lambda cv: captain_value(cv[0], cv[1], predictions, modifier))
    ranked = sorted(
        (pid for pid in starts if pid in predictions),
        key=lambda pid: predictions[pid].expected,
        reverse=True,
    )
    return tuple(ranked[:slots])


def choose_switch(
    starts: Sequence[int],
    bench: Sequence[int],
    roles: Mapping[int, str],
    predictions: Mapping[int, Prediction],
) -> tuple[int, int] | None:
    """`(starter, reserve)` or None.

    The starter is the one least likely to play (`p_play`, then lowest expected); the reserve
    is the best-expected bench player of the **same role**. A starter with no same-role
    reserve available is skipped for the next riskiest, and a reserve not expected to score at
    all is never named.
    """
    def p(pid: int) -> float:
        return predictions[pid].p_play if pid in predictions else 1.0

    def e(pid: int) -> float:
        return predictions[pid].expected if pid in predictions else 0.0

    for starter in sorted(starts, key=lambda pid: (p(pid), e(pid))):
        role = roles.get(starter)
        reserves = [pid for pid in bench if roles.get(pid) == role and e(pid) > 0]
        if reserves:
            return starter, max(reserves, key=e)
    return None

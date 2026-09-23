"""The auto-sub engine against the platform's own substitutions. **Saved responses, no network.**

`fixtures/lineup_reconcile/` holds the match detail of all 12 calculated matches of lega
4103937 (rounds 1-3, Serie A 3-5) exactly as `apileague.match_detail` returned them on
2026-09-22 — 24 sides, of which 18 needed a substitution and 28 players came on. Each line
says who had no vote (`cscr` 100), who went off (`ptype` `U`), who came on (`ptype` `E`),
what each man scored (`cscr`) and who took the positional malus (`m`); each side says the
module it was submitted in (`mdl`), the module it was **scored** in (`nmdl`) and its total
(`tot`). `roles_4103937.json` is the Mantra role of every player who appears, read once from
`league_player_pool` and committed, because this tier opens no socket.

That is enough to replay the engine against 18 real substitutions, and it settles **Open
Question 1**: which of BASIC, EASY and MASTER this lega runs.

**Measured 2026-09-23. EASY reproduces all 18, BASIC 16, MASTER 13.** The two modes that
miss, miss in the same way and it is the discriminating one: they change the module. On
round 1 `18774379` the platform paid a `-1` to keep 4-2-3-1 where a free 3-4-2-1 existed,
and on round 3 `19184924` it paid a `-1` to keep 3-4-2-1 where a free 3-4-1-2 existed —
BASIC's Efficient tier takes the free module both times, and the platform did not. MASTER
misses those two and three more where an earlier bench man is reachable only by changing
module. `nmdl == mdl` on all 24 sides, which is the same fact seen from the other end.

Those five sides are **named** in the test below rather than counted. On 16 of the 18 all
three modes agree, so "BASIC misses 2" is a number a new fixture moves for reasons that
have nothing to do with the modes.

`swtc` was looked at and is **not** the substitution record: it is set on 7 of the 24 sides
while 18 made substitutions, its first field is a bench player and its second a starter, and
its fourth is always `mdl`. It reads as the manager's *declared conditional* switch ("bring
X on for Y and change to module Z"), which never fired here because every declared X was
himself without a vote. Recorded so the next reader does not re-derive it; the engine does
not use it.

⚠ This is evidence about **this lega** (`sstype: 5`), not about the platform: one lega, one
season, one setting. It is why `FANTABOT_LINEUP_SUB_MODE` has no default and the operator
still confirms it (CP4). What the file pins is that the *engine* reproduces the platform
under a stated mode — so a change that breaks the reproduction is caught whatever the
operator then chooses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _paths import FIXTURES

from fantabot.domain.asta.roles import normalize_roles
from fantabot.domain.lineup.scoring import PlatformLine
from fantabot.domain.lineup.substitution import SubMode, substitute

HERE = FIXTURES / "lineup_reconcile"
ROLES_DOC = json.loads((HERE / "roles_4103937.json").read_text(encoding="utf-8"))
#: The lega's `mods`, from the same capture as the roles — the modules a tier may move to.
MODULES: tuple[str, ...] = tuple(ROLES_DOC["_modules"])
ROLES = {int(pid): normalize_roles(codes) for pid, codes in ROLES_DOC["roles"].items()}
#: `ssnum` from `settings/calculate`: five substitutions, the keeper's included.
MAX_SUBS = 5

MATCHES = [
    json.loads(path.read_text(encoding="utf-8")) for path in sorted(HERE.glob("311681_*.json"))
]
SIDES: list[tuple[str, dict[str, Any]]] = [
    (f"{match['mday']}:{match[side]['tid']}", match[side])
    for match in MATCHES
    for side in ("home", "away")
]


def _field(side: dict[str, Any], mode: SubMode) -> Any:
    starts = [PlatformLine.parse(raw) for raw in side["starts"]]
    bench = [PlatformLine.parse(raw) for raw in side["bench"]]
    return substitute(
        module=str(side["mdl"]),
        starts=[line.pid for line in starts],
        bench=[line.pid for line in bench],
        voted=[line.pid for line in (*starts, *bench) if line.vote is not None],
        roles=ROLES,
        mode=mode,
        modules=MODULES,
        max_subs=MAX_SUBS,
    )


def _observed(side: dict[str, Any]) -> tuple[frozenset[int], str, int]:
    """Who came on, the module it was scored in, and how many maluses the platform applied."""
    lines = [PlatformLine.parse(raw) for raw in (*side["starts"], *side["bench"])]
    return (
        frozenset(line.pid for line in lines if line.sub == "E"),
        str(side["nmdl"]),
        sum(line.malus for line in lines),
    )


def test_the_fixture_is_what_this_file_claims() -> None:
    """A reconciliation over the wrong rows reconciles nothing. Every number this file
    reasons from is asserted here, so a re-captured fixture cannot quietly shrink it."""
    assert len(SIDES) == 24
    assert len(ROLES) == 218
    assert all(pid in ROLES for _, side in SIDES for raw in side["starts"] for pid in [raw["pid"]])
    entered = [len(_observed(side)[0]) for _, side in SIDES]
    assert sum(entered) == 28
    assert sum(1 for n in entered if n) == 18


def test_the_module_never_changed_on_any_of_the_twenty_four_sides() -> None:
    """`nmdl == mdl` everywhere. Read alone this is weak — most sides needed no help at all.
    Its force is the two sides below, where keeping the module cost a malus."""
    assert [key for key, side in SIDES if side["mdl"] != side["nmdl"]] == []


@pytest.mark.parametrize(("key", "side"), SIDES, ids=[key for key, _ in SIDES])
def test_easy_reproduces_every_substitution(key: str, side: dict[str, Any]) -> None:
    entered, module, malus = _observed(side)
    outcome = _field(side, "easy")

    assert frozenset(outcome.entered) == entered
    assert outcome.module == module
    assert outcome.malus == malus


@pytest.mark.parametrize(("key", "side"), SIDES, ids=[key for key, _ in SIDES])
def test_easy_reproduces_every_total_within_a_hundredth(key: str, side: dict[str, Any]) -> None:
    """`tot` is the sum of the fielded XI's `cscr`, malus already inside it. The engine has
    to pick the same eleven for the sum to land, so this is the substitution test again with
    the platform's own arithmetic on top."""
    score = {
        line.pid: line.score
        for line in (PlatformLine.parse(raw) for raw in (*side["starts"], *side["bench"]))
    }
    outcome = _field(side, "easy")

    fielded = sum(score[pid] or 0.0 for pid in outcome.fielded if pid is not None)
    assert fielded == pytest.approx(float(side["tot"]), abs=0.01)


def _disagreement(mode: SubMode) -> list[str]:
    """The sides this mode fails to reproduce, named and sorted. Only the 18 that needed a
    substitution are judged: on the other six the engine does not run at all."""
    out: list[str] = []
    for key, side in SIDES:
        observed = _observed(side)
        if not observed[0]:
            continue
        outcome = _field(side, mode)
        if (frozenset(outcome.entered), outcome.module, outcome.malus) != observed:
            out.append(key)
    return sorted(out)


def test_the_three_modes_are_told_apart_by_the_eighteen() -> None:
    """The measurement itself, pinned. EASY is exact; the other two are not, and the sides
    are **named** rather than counted. On 16 of the 18 all three modes agree, so a count is
    a number that a new fixture moves for no reason — the evidence is these sides and no
    others, and an implementation that quietly makes one of them fit has to say which."""
    assert _disagreement("easy") == []
    assert _disagreement("basic") == ["1:18774379", "3:19184924"]
    assert _disagreement("master") == [
        "1:18774379", "2:18780035", "2:18814141", "3:18814141", "3:19184924",
    ]


def test_where_basic_and_the_platform_part_company_the_platform_paid_a_malus() -> None:
    """The two BASIC misses are the Efficient tier firing: a free module change existed and
    the platform took the `-1` instead. That is what makes this lega EASY rather than a lega
    whose evidence happens to be silent."""
    for key in _disagreement("basic"):
        side = dict(SIDES)[key]
        _entered, _module, malus = _observed(side)
        basic = _field(side, "basic")

        assert malus == 1, key
        assert basic.malus == 0 and basic.module != side["mdl"], key

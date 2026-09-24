"""Compose the weekly formation: roster -> value -> best module -> bench -> `PlannedLineup`.

The one place the lineup value model is assembled, mirroring `application/asta_planner` for
the auction. Pure orchestration of the `domain/lineup` pieces: the inputs (roster ids, the
role/value maps, the allowed modules, the matchday coordinates) are gathered by the interface
from `apileague`'s `teamLineup` and handed in as `LineupInputs`, so this module opens no
socket and reads no clock.

The per-player value is `domain/lineup/predict`'s blended score when `predictions` are handed
in, and the platform's `indexCompare` otherwise — the latter is the `--no-predict` ablation and
reproduces the pre-predictor plan exactly. Captain and switch (`domain/lineup/extras`) are
chosen per plan, from the predictions, and only where the lega enables them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from fantabot.domain.asta.roles import normalize_roles
from fantabot.domain.classic.formations import DEFENCE_MODIFIER_FORMATIONS
from fantabot.domain.classic.roles import normalize_roles as classic_normalize_roles
from fantabot.domain.classic.roles import role_from_fcrle
from fantabot.domain.lineup import schema
from fantabot.domain.lineup.bench import GK_ROLE, order_bench
from fantabot.domain.lineup.build import ranked_lineups
from fantabot.domain.lineup.defence import (
    DEFENDERS_FOR_BONUS,
    expected_defence_bonus,
    p_defenders_voting,
    swapped_module,
)
from fantabot.domain.lineup.errors import NoFieldableModule
from fantabot.domain.lineup.extras import (
    captain_slots,
    captain_value,
    choose_captains,
    choose_defence_switch,
    choose_switch,
    switch_cross_role,
    switch_enabled,
)
from fantabot.domain.lineup.marle import roles_from_marle
from fantabot.domain.lineup.models import PlannedLineup, assemble_roster
from fantabot.domain.lineup.predict import Prediction
from fantabot.domain.lineup.rules import LeagueRules
from fantabot.domain.lineup.value import score

#: The Classic goalkeeper role for the bench's slot 0, the counterpart to Mantra's `POR`.
CLASSIC_GK_ROLE = "P"


@dataclass(frozen=True)
class LineupInputs:
    """Everything the plan needs, already fetched. Assembled by the interface shell."""

    roster_ids: Sequence[int]
    roles_by_id: Mapping[int, Sequence[str]]
    fvmma_by_id: Mapping[int, float]
    modules: Sequence[str]
    competition: int
    mday: int
    cmday: int
    tid: int
    bench_size: int
    #: The saved lineup's `allComp`, carried to the payload unchanged.
    all_comp: bool = False
    #: How many ids `capt` carries (0 = no captain) and whether a switch is sent.
    captain_slots: int = 0
    switch_enabled: bool = False
    #: `domain/lineup/predict` output. None ranks on `indexCompare` and sends no captain or
    #: switch — the pre-predictor behaviour, field for field.
    predictions: Mapping[int, Prediction] | None = None
    #: The lega plays the *modificatore difesa* (earned with >= 4 defenders voting): see
    #: `plan_lineups` for how a back four/five is preferred and when the risk vetoes it.
    defence_modifier: bool = False
    #: The switch may bring on another role and change the module (`lswi` 3).
    switch_cross_role: bool = False
    #: The lega's `settings/calculate` rules — the modifier's bands, the substitution cap.
    #: None when that read failed: the planner then falls back to preferring any back four.
    rules: LeagueRules | None = None
    #: `"mantra"` or `"classic"` — selects the role source, the slot provider, the normalizer
    #: and the bench keeper role. Defaults to Mantra so existing callers are unchanged.
    fmt: str = "mantra"


def inputs_from_lineup(
    dto: Mapping[str, Any],
    lineup_info: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    competition: int,
    *,
    tid: int,
    fmt: str = "mantra",
) -> tuple[LineupInputs, dict[int, str]]:
    """Turn a `teamLineup_read` response + `settings/lineup` into `LineupInputs` and a
    id->name map. Pure.

    The roster, roles and value all come from `lineUpInfo` (`docs/leghe-api.md`): `role` is
    the role codes, `indexCompare` is the platform's value signal, `plyr` the name. `lcap` and
    `lswi` from `settings` say whether a captain and a switch are sent. `predictions` are not
    set here: they need the database's history, and are attached by the caller.

    `tid` is passed in from `apileague.my_team` (authoritative) rather than read from `dto`,
    which is empty when the competition has no saved lineup — a state that would otherwise
    submit `tid=0`. The matchday coordinates still come from `dto`; the submit path refuses
    to POST when they are absent (0).
    """
    roster_ids: list[int] = []
    roles_by_id: dict[int, list[str]] = {}
    value_by_id: dict[int, float] = {}
    names: dict[int, str] = {}
    for row in lineup_info:
        pid = int(row["pid"])
        roster_ids.append(pid)
        # Classic reads the single macro role on the {1:P,2:D,3:C,4:A} scale: from `fcrle` when
        # present, else from `role` — the live Classic `lineUpInfo` (measured 2026-09-22) has no
        # `fcrle` and carries that same integer as a one-element `role` list. Mantra reads the
        # granular marle codes. A Classic row with neither yields no role and fails closed at
        # assemble_roster rather than being read on the wrong scale.
        if fmt == "classic":
            code = row.get("fcrle")
            if code is None and row.get("role"):
                code = row["role"][0]
            roles_by_id[pid] = [role_from_fcrle(code)] if code is not None else []
        else:
            roles_by_id[pid] = roles_from_marle(row.get("role") or [])
        value_by_id[pid] = float(row.get("indexCompare") or 0.0)
        names[pid] = str(row.get("plyr", pid))

    inputs = LineupInputs(
        roster_ids=roster_ids,
        roles_by_id=roles_by_id,
        fvmma_by_id=value_by_id,
        modules=list(settings.get("mods", [])),
        competition=competition,
        mday=int(dto.get("mday", 0)),
        cmday=int(dto.get("cmday", 0)),
        tid=tid,
        bench_size=_bench_size(settings, dto, rosa=len(roster_ids)),
        all_comp=bool(dto.get("allComp", False)),
        captain_slots=captain_slots(settings.get("lcap")),
        switch_enabled=switch_enabled(settings.get("lswi")),
        # Every one of the operator's Classic leghe plays the modificatore difesa (his account,
        # 2026-09-23); the platform's settings expose no flag for it that has been measured.
        defence_modifier=fmt == "classic",
        switch_cross_role=switch_cross_role(settings.get("lswi")),
        fmt=fmt,
    )
    return inputs, names


def with_rules(inputs: LineupInputs, rules: LeagueRules) -> LineupInputs:
    """`inputs` with the lega's own rules attached. The rules, not the format, now say
    whether the modificatore difesa is played at all."""
    return replace(inputs, rules=rules, defence_modifier=rules.defence is not None)


def _bench_size(settings: Mapping[str, Any], dto: Mapping[str, Any], *, rosa: int) -> int:
    """`tbench`, or — when the lega sets 0 — the size of the bench the platform already saved.

    `tbench` 0 is not "no bench": lega 4219373 reads 0 and its saved bench held 13 of 14
    reserves (measured 2026-09-23). So the saved lineup is the authority, and with none saved
    every reserve goes on the bench.
    """
    tbench = int(settings.get("tbench", 12) or 0)
    if tbench > 0:
        return tbench
    saved = len(dto.get("bench") or [])
    return saved if saved > 0 else max(rosa - 11, 0)


def plan_lineups(inputs: LineupInputs) -> list[PlannedLineup]:
    """Every fieldable `PlannedLineup`, best first.

    The submit path walks this list, falling to the next module if the platform rejects one
    (a wrong schema is survived, not fatal). Raises the `domain/lineup` errors
    (`RosterIncomplete`, `NoFieldableModule`, `BenchIncomplete`) unchanged.

    **Modificatore difesa.** With the lega's table (`rules.defence`) and predictions, a back
    four or five is credited `P(four defenders vote) * E[bonus]` on top of its summed score —
    `P` under the lega's substitution cap, `E[bonus]` over its own vote bands — and every plan
    is then ranked on that total. A risky back four wins only when the bonus it chases is worth
    the points it costs.

    On a lega whose switch crosses roles (3677376) a back four may instead set the switch
    riskiest defender -> best bench midfielder (`swtcMdl` the module after the swap). That
    variant is taken when it is worth more: the bonus is then earned only if the risky
    defender plays, but when he does not the midfielder's points replace a bench defender's.

    Without predictions (`--no-predict`), or without a readable table, there is nothing to
    weigh: every back four or five simply ranks ahead of the other modules, as before.
    """
    classic = inputs.fmt == "classic"
    roster = assemble_roster(
        inputs.roster_ids,
        roles_by_id=inputs.roles_by_id,
        fvmma_by_id=inputs.fvmma_by_id,
        normalize=classic_normalize_roles if classic else normalize_roles,
    )
    predictions = inputs.predictions
    scores = (
        {p.id: predictions[p.id].score if p.id in predictions else 0.0 for p in roster}
        if predictions is not None
        else score(roster)
    )
    macro_role = {p.id: min(p.roles) for p in roster}
    slots_provider = schema.classic_slots if classic else schema.slots
    gk_role = CLASSIC_GK_ROLE if classic else GK_ROLE
    ranked = ranked_lineups(roster, inputs.modules, value=scores, slots_provider=slots_provider)
    modifier = inputs.rules.defence if inputs.rules is not None else None
    max_subs = (
        inputs.rules.subs.max_subs
        if inputs.rules is not None and inputs.rules.subs is not None
        else None
    )
    captain_modifier = inputs.rules.captain if inputs.rules is not None else None
    weigh_bonus = inputs.defence_modifier and predictions is not None and modifier is not None

    def p_of(pid: int) -> float:
        return predictions[pid].p_play if predictions and pid in predictions else 1.0

    def e_of(pid: int) -> float:
        return predictions[pid].expected if predictions and pid in predictions else 0.0

    plans: list[PlannedLineup] = []
    totals: list[float] = []
    back_four: list[bool] = []
    for module, starts in ranked:
        bench = order_bench(roster, starts, value=scores, size=inputs.bench_size, gk_role=gk_role)
        captains: tuple[int, ...] = ()
        switch: tuple[int, int] | None = None
        switch_module: str | None = None
        bonus_p: float | None = None
        avg_vote: float | None = None
        bonus_ev: float | None = None
        captain_ev: float | None = None
        total = sum(scores.get(pid, 0.0) for pid in starts)
        is_back_four = inputs.defence_modifier and module in DEFENCE_MODIFIER_FORMATIONS
        if predictions is not None:
            captains = choose_captains(
                starts, predictions, slots=inputs.captain_slots, modifier=captain_modifier
            )
            if captain_modifier is not None and captains:
                captain_ev = captain_value(
                    captains[0], captains[1] if len(captains) > 1 else None,
                    predictions, captain_modifier,
                )
                total += captain_ev
            if is_back_four and modifier is not None:
                d_start = [p for p in starts if macro_role[p] == "D"]
                d_bench = [p for p in bench if macro_role[p] == "D"]
                others = [p_of(p) for p in starts if macro_role[p] != "D"]
                keeper = next((p for p in starts if macro_role[p] == gk_role), None)
                avg_vote, e_bonus = expected_defence_bonus(
                    (
                        predictions[keeper].vote_if_plays
                        if keeper is not None and keeper in predictions
                        else None
                    ),
                    [predictions[p].vote_if_plays for p in d_start if p in predictions],
                    modifier,
                )
                bonus_p = p_defenders_voting(
                    [p_of(p) for p in d_start], [p_of(p) for p in d_bench],
                    max_subs=max_subs, others=others,
                )
                bonus_ev = bonus_p * e_bonus
                if inputs.switch_enabled and inputs.switch_cross_role:
                    cover = choose_defence_switch(starts, bench, macro_role, predictions)
                    swapped = swapped_module(
                        module, from_role="D", to_role="C", allowed=inputs.modules
                    )
                    if cover is not None and swapped is not None:
                        risky, mid = cover
                        # With the switch the risky defender's absence brings a midfielder,
                        # so the bonus needs him to play and three of the rest to vote.
                        p_cover = p_of(risky) * p_defenders_voting(
                            [p_of(p) for p in d_start if p != risky],
                            [p_of(p) for p in d_bench],
                            need=DEFENDERS_FOR_BONUS - 1, max_subs=max_subs, others=others,
                        )
                        best_bench_d = max((e_of(p) for p in d_bench), default=0.0)
                        swap_gain = (1.0 - p_of(risky)) * (e_of(mid) - best_bench_d)
                        if p_cover * e_bonus + swap_gain > bonus_ev:
                            switch, switch_module = cover, swapped
                            bonus_p, bonus_ev = p_cover, p_cover * e_bonus + swap_gain
                total += bonus_ev
            if inputs.switch_enabled and switch is None:
                switch = choose_switch(starts, bench, macro_role, predictions)
        totals.append(total)
        back_four.append(is_back_four)
        plans.append(
            PlannedLineup(
                module=module,
                starts=tuple(starts),
                bench=tuple(bench),
                competition=inputs.competition,
                mday=inputs.mday,
                cmday=inputs.cmday,
                tid=inputs.tid,
                all_comp=inputs.all_comp,
                captains=captains,
                switch=switch,
                switch_module=switch_module,
                defence_bonus_p=bonus_p,
                defence_avg_vote=avg_vote,
                defence_bonus_ev=bonus_ev,
                max_subs=max_subs if bonus_p is not None else None,
                captain_bonus_ev=captain_ev,
            )
        )
    if weigh_bonus or captain_modifier is not None:
        order = sorted(range(len(plans)), key=lambda i: -totals[i])
        plans = [plans[i] for i in order]
    elif inputs.defence_modifier:
        # Nothing to weigh the bonus with: every back four/five first, best-sum within.
        order = sorted(range(len(plans)), key=lambda i: not back_four[i])
        plans = [plans[i] for i in order]
    if not plans:
        raise NoFieldableModule(tuple(inputs.modules))
    return plans


def plan_lineup(inputs: LineupInputs) -> PlannedLineup:
    """The single best `PlannedLineup` from `inputs`."""
    return plan_lineups(inputs)[0]

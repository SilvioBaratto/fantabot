"""`payload.build` — a `PlannedLineup` into the `gaming/v1/teamLineup` POST body. Pure.

Pinned field-for-field against the body captured live 2026-09-02 (`docs/leghe-api.md`): the
platform validates it strictly, so a missing or misnamed field is a `400`. `capt` stays
empty and `swtcMdl` mirrors `mdl` for this phase (SPEC open Qs 1-2).
"""

from __future__ import annotations

from fantabot.domain.lineup.models import PlannedLineup
from fantabot.domain.lineup.payload import build

PLAN = PlannedLineup(
    module="343",
    starts=tuple(range(1, 12)),
    bench=tuple(range(20, 32)),
    competition=311681,
    mday=1,
    cmday=3,
    tid=10000003,
)


def test_the_body_has_exactly_the_captured_fields() -> None:
    body = build(PLAN)

    assert set(body) == {
        "starts", "bench", "capt", "mdl", "idcomp", "mday", "cmday", "tid",
        "allComp", "visb", "swtcA", "swtcB", "swtc", "swtcMdl",
    }


def test_the_lineup_fields_pass_through() -> None:
    body = build(PLAN)

    assert body["starts"] == list(range(1, 12)) and len(body["starts"]) == 11
    assert body["bench"] == list(range(20, 32)) and len(body["bench"]) == 12
    assert body["mdl"] == "343"
    assert (body["idcomp"], body["mday"], body["cmday"], body["tid"]) == (311681, 1, 3, 10000003)


def test_the_phase_constants_match_the_captured_body() -> None:
    body = build(PLAN)

    assert body["capt"] == []
    assert body["swtcMdl"] == "343"  # mirrors mdl
    assert body["swtcA"] == body["swtcB"] == body["swtc"] == 0
    assert body["allComp"] is False
    assert body["visb"] is True

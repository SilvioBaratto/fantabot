"""The default lineup path, byte for byte: the plan the hourly job builds and the POST it sends.

Captured at Checkpoint 0 (SPEC A21) and the "default unchanged" baseline from then on:
every later task adds the projection *beside* this path, and the one thing it may not do is
move these bytes. `tests/golden/lineup/` holds the six reads the job makes for lega
4103937 — `my_team`, `competitions`, `teamLineup_read`, `lineup_settings`,
`roster_settings`, `league_status` — exactly as the platform returned them on
2026-09-22. They carry no token. They are served back through fakes into the real
`submit_lineup`, armed and scheduled as `launchd` runs it, with the clock pinned to the
capture time and a fake POST that records its body.

The rules are `test_golden.py`'s: `FANTABOT_GOLDEN_UPDATE=1` rewrites the expected file,
and that run then fails on purpose; the inputs are pinned by `MANIFEST.sha256`.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime
from typing import Any

import pytest
from _golden import GOLDEN

UPDATE = os.environ.get("FANTABOT_GOLDEN_UPDATE") == "1"

HERE = GOLDEN / "lineup"
EXPECTED = HERE / "expected_default_submit.json"
LEAGUE = 4103937
CAPTURED_AT = datetime.fromisoformat((HERE / "captured_at.txt").read_text().strip())


def _read(name: str) -> Any:
    return json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))


def render(outcome: Any, posted: list[Any]) -> str:
    """The decision, as stable bytes: every ranked plan, the walk, and what was POSTed."""
    document = {
        "plans": [dataclasses.asdict(plan) for plan in outcome.plans],
        "refused": outcome.refused,
        "rejected": [list(pair) for pair in outcome.rejected],
        "posted": posted,
    }
    return json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from fantabot.adapters.http import apileague

    for name in (
        "my_team", "competitions", "teamLineup_read", "lineup_settings", "roster_settings",
        "league_status",
    ):
        body = _read(name)
        monkeypatch.setattr(apileague, name, lambda *a, _body=body, **k: _body)
    sent: list[Any] = []

    def submit(_league: int, body: Any, **_: Any) -> dict[str, Any]:
        sent.append(body)
        return {}

    monkeypatch.setattr(apileague, "teamLineup_submit", submit)
    return sent


def test_the_default_scheduled_submit_is_byte_identical(posted: list[Any]) -> None:
    from fantabot.application.lineup_submit import submit_lineup

    outcome = submit_lineup(
        object(),  # the store: every read is faked, so nothing ever asks it for a token
        league_id=LEAGUE,
        arm=True,
        auto_act=True,
        scheduled=True,
        now=lambda: CAPTURED_AT,
    )
    actual = render(outcome, posted)

    if UPDATE:
        EXPECTED.write_text(actual, encoding="utf-8")
        return

    assert len(posted) == 1, "the armed default path POSTs exactly once"
    assert actual == EXPECTED.read_text(encoding="utf-8"), (
        "the default lineup path changed. If that is a regression, fix the code; if it is "
        "deliberate, regenerate with FANTABOT_GOLDEN_UPDATE=1 and defend it in the commit."
    )

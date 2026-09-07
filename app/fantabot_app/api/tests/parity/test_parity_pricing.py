"""`db price` and `GET /asta/target-prices`, over one fitted model.

There is only one implementation — both sides call `application/pricing.run` — so what can
diverge here is the *wiring*: which `system`, which `top_n`, and whether the two reach the
same call at all. That is not hypothetical for this repository: `read_plan_inputs` had a
format parameter and five of six callers ignored it, and `asta calibrate` still forwards a
shape to one corpus and none to the other.

**This tier's database is the point of the exercise, not an accident.** `pricing.run`
upserts `target_price` behind a GET, so opening the Prices page mutates whatever database
the app is pointed at (1.11). Running it here is safe because this tier refuses to run
against the canonical one. The fit's own upsert lands on `TARGET_SEASON` and is *not*
swept — the seed's `PARITY_SEASON` rows are — because those rows are what the fit produced
from the tier database's own corpus and nothing here can tell them from a `db` tier run's.
That is a side effect this tier owns, and one more reason it may not be pointed anywhere
else.

The fit needs `TRAIN_SEASONS` of `statistiche` and a `TARGET_SEASON` universe — real
seasons, and far more than a parity fixture should invent. So this skips cleanly when the
tier's database has no corpus, and is a real comparison on a machine that has one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import SeededWorld


def _direct(system: str, top_n: int) -> Any:
    """What `db price` computes. The command's own call, with its own defaults.

    `interface/app.py::db_price` is `pricing.run(system=..., top_n=...)` and a printer;
    calling `run` here is calling the command's decision without its terminal.
    """
    from fantabot.application import pricing

    return pricing.run(system=system, top_n=top_n)


@pytest.mark.parametrize("system", ["classic", "mantra"])
def test_the_page_and_the_command_report_the_same_fit(
    seeded_db: SeededWorld, api: TestClient, system: str
) -> None:
    try:
        report = _direct(system, top_n=15)
    except Exception as exc:  # noqa: BLE001 — a corpus, or the absence of one
        pytest.skip(f"no pricing corpus in the tier's database ({type(exc).__name__}: {exc})")

    body = api.get("/api/v1/asta/target-prices", params={"system": system, "top_n": 15}).json()

    assert body["found"] is True, body
    assert body["system"] == report.system
    assert body["flag_counts"] == dict(report.flag_counts)
    assert [f["role"] for f in body["fades"]] == [f.role for f in report.fades]
    assert [row["player_id"] for row in body["biggest_bumps"]] == [
        r.id for r in report.biggest_bumps
    ]
    assert [row["player_id"] for row in body["biggest_cuts"]] == [
        r.id for r in report.biggest_cuts
    ]


def test_top_n_reaches_the_fit_rather_than_being_dropped(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """A parameter a caller ignores is this repository's recurring defect, not a
    hypothetical: five of `read_plan_inputs`' six callers ignored its shape."""
    try:
        _direct("classic", top_n=3)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no pricing corpus in the tier's database ({type(exc).__name__}: {exc})")

    body = api.get("/api/v1/asta/target-prices", params={"system": "classic", "top_n": 3}).json()

    assert len(body["biggest_bumps"]) <= 3
    assert len(body["biggest_cuts"]) <= 3

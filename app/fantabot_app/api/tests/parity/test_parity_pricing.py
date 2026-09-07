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
from unittest.mock import patch

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


def _skip_without_a_corpus(report: Any) -> None:
    """`pricing.run` does not raise on an empty corpus — it returns an empty report.

    That is why 1.7 gave the endpoint a `no_data` outcome: an empty report used to render
    as an empty table under a confident heading, which reads as "the model says nothing
    moved". A parity test comparing two empty reports is the same failure one level up.
    """
    if not (report.fades or report.biggest_bumps or report.biggest_cuts):
        pytest.skip("no pricing corpus in the tier's database — the fit produced nothing")


@pytest.mark.parametrize("system", ["classic", "mantra"])
def test_the_page_and_the_command_report_the_same_fit(
    seeded_db: SeededWorld, api: TestClient, system: str
) -> None:
    try:
        report = _direct(system, top_n=15)
    except Exception as exc:  # noqa: BLE001 — a corpus, or the absence of one
        pytest.skip(f"no pricing corpus in the tier's database ({type(exc).__name__}: {exc})")
    _skip_without_a_corpus(report)

    body = api.get("/api/v1/asta/target-prices", params={"system": system, "top_n": 15}).json()

    assert body["found"] is True, body
    assert body["outcome"] == "priced"
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
        _skip_without_a_corpus(_direct("classic", top_n=3))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no pricing corpus in the tier's database ({type(exc).__name__}: {exc})")

    body = api.get("/api/v1/asta/target-prices", params={"system": "classic", "top_n": 3}).json()

    assert len(body["biggest_bumps"]) <= 3
    assert len(body["biggest_cuts"]) <= 3


def test_the_get_writes_nothing(seeded_db: SeededWorld, api: TestClient) -> None:
    """Proven by putting the endpoint's **own** session inside a read-only transaction.

    `pricing.run` upserts `target_price`, and the GET called it — so opening the Prices page
    mutated the database. Not a slow GET: a page whose refresh is an action, on a route a
    browser is free to prefetch, retry, or render twice. The upsert is idempotent, which is
    exactly why nobody noticed.

    **The first version of this test proved nothing.** It opened a `READ ONLY` transaction on
    a session of its own, then called the endpoint — which opens its own session through
    `database_manager`. The GET never ran inside the read-only transaction and the test would
    have passed had it written. Postgres has to be the one refusing, and it has to be
    refusing *the session the endpoint actually uses*, so the manager itself is wrapped here.
    """
    from contextlib import contextmanager

    from fantabot.adapters.persistence import database_manager
    from sqlalchemy import text

    real = database_manager.get_session
    opened: list[str] = []

    @contextmanager
    def read_only():  # type: ignore[no-untyped-def]
        with real() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            opened.append("yes")
            yield session

    # The guard on the guard: prove the wrapper really refuses a write, or this test passes
    # on a session that would have accepted one.
    with read_only() as probe, pytest.raises(Exception, match="(?i)read.only"):
        probe.execute(
            text("INSERT INTO teams (stagione, codice, nome_completo) VALUES "
                 "('1999/00', 'ZZZ', 'proof')")
        )
    opened.clear()

    with patch.object(database_manager, "get_session", read_only):
        body = api.get("/api/v1/asta/target-prices", params={"system": "classic"}).json()

    assert opened, "the endpoint never opened a session — the patch did not take"
    # Either a report or a named refusal. Both are fine; a write would have raised inside
    # the endpoint and come back as `unreachable` with a read-only error in the reason.
    assert body["outcome"] in {"priced", "no_data"}, body
    if body["outcome"] == "priced":
        assert body["stored"] == 0, "the GET reported storing rows"


def test_the_read_only_wrapper_would_catch_a_write(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """The negative control for the test above. Without it, a wrapper that silently stopped
    applying would look exactly like a GET that does not write.

    It substitutes a `fit` that writes through `database_manager` — which is structurally
    what `pricing.run` does — rather than calling `run` itself, so the control does not need
    a training corpus the tier database has none of. A control that skips is not a control.
    """
    from contextlib import contextmanager

    from fantabot.adapters.persistence import database_manager
    from fantabot.application import pricing
    from sqlalchemy import text

    real = database_manager.get_session

    @contextmanager
    def read_only():  # type: ignore[no-untyped-def]
        with real() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            yield session

    def writes(**_kwargs: object) -> object:
        with database_manager.get_session() as session:
            session.execute(
                text("INSERT INTO teams (stagione, codice, nome_completo) VALUES "
                     "('1999/00', 'ZZZ', 'proof')")
            )
        # Deliberately does NOT name the phrase the assertion greps for: an earlier version
        # said "inside a read-only transaction" here, the endpoint rendered *this* message
        # into `reason`, and the test matched its own sentinel. It passed with the wrapper
        # disabled.
        raise AssertionError("SENTINEL: the insert was accepted")

    with (
        patch.object(database_manager, "get_session", read_only),
        patch.object(pricing, "fit", writes),
    ):
        body = api.get("/api/v1/asta/target-prices", params={"system": "classic"}).json()

    assert body["outcome"] == "unreachable", (
        "a write inside the endpoint's own session was not refused — the wrapper is not "
        f"reaching it, so the test above proves nothing. Got: {body}"
    )
    # Postgres's own words, not ours. `because()` renders `InternalError:
    # (psycopg2.errors.ReadOnlySqlTransaction) cannot execute INSERT in a read-only
    # transaction`; the sentinel above cannot produce that phrase.
    reason = (body["reason"] or "").lower()
    assert "read-only transaction" in reason, body
    assert "sentinel" not in reason, (
        "the insert was accepted — the wrapper is not reaching the endpoint's session, so "
        "the test above proves nothing"
    )


def test_the_post_is_where_the_write_lives(seeded_db: SeededWorld, api: TestClient) -> None:
    """A POST beside the GET rather than a flag on it: the method is the contract a
    browser, a proxy and an operator all read."""
    try:
        _skip_without_a_corpus(_direct("classic", top_n=15))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no pricing corpus in the tier's database ({type(exc).__name__}: {exc})")

    body = api.post("/api/v1/asta/target-prices", params={"system": "classic"}).json()

    assert body["outcome"] == "priced"
    assert body["stored"] > 0, "the POST stored nothing"

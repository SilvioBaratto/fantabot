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
seasons, under names `PARITY_SEASON` cannot borrow. Every test here used to open by
skipping when the tier's database had none, which made each of them a real comparison on a
machine with a corpus and a green tick on one without.

**The seed carries its own corpus now and the skips are gone** (1.16). That was not
tidying: `upsert_target_price` returns before issuing any SQL when handed no rows, so
`test_the_get_writes_nothing` ran its read-only transaction against nothing to refuse, and
the Accept clause it was written for was unproven rather than proven. Removing the guards
also ran four assertions for the first time — and one of them had never been able to pass:
it read `player_id` off a response whose field is `id`.
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


@pytest.mark.parametrize("system", ["classic", "mantra"])
def test_the_tier_seeds_a_corpus_both_halves_of_the_model_reach(
    seeded_db: SeededWorld, system: str
) -> None:
    """The contract every other test in this file rests on, asserted once and first.

    Each of them used to open with a skip, and a skip is what 1.16 is about:
    `upsert_target_price` returns at `scraping.py:193` before issuing any SQL when there
    are no rows, so on a corpus-less database `test_the_get_writes_nothing` runs a
    read-only transaction with nothing to refuse — and passes. The skip kept that from
    reading as a green, and left the property unproven rather than proving it.

    The seed now carries its own corpus, so the guards are gone and this stands in their
    place. It fails loudly, here, if the seed stops producing one — rather than four
    tests quietly going green by not running.

    Both halves are named separately because they fail for different reasons and are
    repaired in different tables. The **universe** is `quotazioni` under
    `TARGET_SEASON`; without it there is no write to refuse. The **fades** are
    `quotazioni` under `TRAIN_SEASONS` — reached through the `qi_bias` view, not a table
    of its own — joined to `statistiche` for each training season's prior; without them
    the report still prices (at `qi`, flagged) and only the `fades` comparison goes
    vacuous.
    """
    from fantabot.application import pricing

    report = pricing.fit(system=system, top_n=15)

    assert report.biggest_bumps and report.biggest_cuts, (
        f"no {system} universe under {pricing.TARGET_SEASON} — `upsert_target_price` "
        "returns before issuing SQL when there are no rows, so a read-only transaction "
        "has nothing to refuse and `test_the_get_writes_nothing` proves nothing"
    )
    assert {f.role for f in report.fades} == set(seeded_db.pricing_roles), (
        f"the {system} fit produced {sorted(f.role for f in report.fades)}; the seed's "
        "training cohort covers the three outfield macro roles, and `GK` is excluded by "
        "`training_pairs` by design"
    )
    assert all(f.observations == seeded_db.pricing_observations for f in report.fades), (
        "a role fitted on fewer observations than `MIN_OBSERVATIONS` is dropped by "
        f"`fit_fades` in silence, so this is the seed shrinking: {report.fades}"
    )
    assert seeded_db.pricing_observations >= pricing.MIN_OBSERVATIONS, (
        "the seed's own cohort is below the threshold `fit_fades` drops a role at, so "
        "the assertion above is checking a number that cannot produce a fade"
    )

    # **A fitted fade nothing is priced through is not a corpus.** Non-emptiness alone is
    # too weak to say so: the seed's four flag players keep the universe non-empty by
    # themselves, so dropping the whole faded cohort left every assertion above green.
    # `predicted_pct_delta` is set on exactly the rows that reached `fades[bucket]`.
    priced = (*report.biggest_bumps, *report.biggest_cuts)
    assert any(row.predicted_pct_delta is not None for row in priced), (
        "no player in the universe reached the fade branch, so the fitted line is "
        "applied to nobody and `price_universe`'s main path is never run"
    )
    # And the four branches that are not the fade, each of which is a different screen
    # for the operator. `flag_counts` strips a flag's parenthesised argument, so these
    # are bare names; `team_discount` is absent by design — see `PRICING_CLUBS`.
    assert set(report.flag_counts) == set(seeded_db.pricing_flags), (
        f"the {system} report's flags are {sorted(report.flag_counts)}, the seed intends "
        f"{sorted(seeded_db.pricing_flags)}"
    )


@pytest.mark.parametrize("system", ["classic", "mantra"])
def test_the_page_and_the_command_report_the_same_fit(
    seeded_db: SeededWorld, api: TestClient, system: str
) -> None:
    report = _direct(system, top_n=15)

    body = api.get("/api/v1/asta/target-prices", params={"system": system, "top_n": 15}).json()

    assert body["found"] is True, body
    assert body["outcome"] == "priced"
    assert body["system"] == report.system
    assert body["flag_counts"] == dict(report.flag_counts)
    assert [f["role"] for f in body["fades"]] == [f.role for f in report.fades]
    # `id`, not `player_id`. The response model is `endpoints/pricing.TargetPrice` and it
    # has never had a `player_id` field — this raised `KeyError` the first time the seed
    # let it run, which is what four years of skipping buys.
    assert [row["id"] for row in body["biggest_bumps"]] == [r.id for r in report.biggest_bumps]
    assert [row["id"] for row in body["biggest_cuts"]] == [r.id for r in report.biggest_cuts]


def test_top_n_reaches_the_fit_rather_than_being_dropped(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """A parameter a caller ignores is this repository's recurring defect, not a
    hypothetical: five of `read_plan_inputs`' six callers ignored its shape."""
    assert _direct("classic", top_n=3).biggest_bumps

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
    with read_only() as probe, pytest.raises(Exception, match=r"(?i)read.only"):
        probe.execute(
            text("INSERT INTO teams (stagione, codice, nome_completo) VALUES "
                 "('1999/00', 'ZZZ', 'proof')")
        )
    opened.clear()

    with patch.object(database_manager, "get_session", read_only):
        body = api.get("/api/v1/asta/target-prices", params={"system": "classic"}).json()

    assert opened, "the endpoint never opened a session — the patch did not take"
    # **`no_data` is not proof and is refused as a result rather than skipped past.**
    # With no corpus `upsert_target_price` returns at `scraping.py:193` before issuing any
    # SQL, so the read-only transaction has nothing to refuse and this test would pass with
    # the GET routed straight back through `pricing.run`. This used to skip there; the seed
    # now guarantees a corpus, so reaching that state means the seed broke — which is a
    # failure, and `test_the_tier_seeds_a_corpus_both_halves_of_the_model_reach` says so
    # first and in more detail.
    assert body["outcome"] != "unreachable", (
        "the GET wrote through the endpoint's own session and Postgres refused it — the "
        f"defect 1.11 fixed, back: {body}"
    )
    assert body["outcome"] == "priced", (
        "the fit found nothing to write, so the read-only transaction had nothing to "
        f"refuse and this proves nothing about the GET: {body}"
    )
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
    body = api.post("/api/v1/asta/target-prices", params={"system": "classic"}).json()

    assert body["outcome"] == "priced"
    # A floor, not an equality: a tier database that also holds a real `TARGET_SEASON`
    # corpus prices that too, and this tier is only forbidden the *canonical* database.
    assert body["stored"] >= seeded_db.pricing_universe, (
        f"the POST stored {body['stored']} rows against a seeded universe of "
        f"{seeded_db.pricing_universe} — some branch of the seed is no longer priced"
    )

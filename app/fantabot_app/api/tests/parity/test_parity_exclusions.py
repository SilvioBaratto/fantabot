"""`db exclude` / `db exclusions` and `/db/exclusions`, over one application function.

An exclusion is the only thing in the engine that can drop a player the listone still
carries — the scraper reproduces the site, and the sentiment gate is floored so news
tilts a value and never vetoes it. It is also **invisible everywhere else**: the plan it
changes does not say a player was removed, so the list is the only screen on which the
fact exists at all.

That makes this tier's usual argument sharper than usual. If the form accepted what the
command refuses, the rows it wrote would be rows the command's own list cannot explain —
and nobody would find out from a plan, because a plan built without a player looks
exactly like a plan built with one nobody wanted.

**Compared against the shared call, not against each other's text.** The tier's rule is
*compare decision content, never rendered text*: the command's output is Rich's, and
asserting on it would pin formatting. So the endpoint is compared row-for-row with
`read_exclusions()`, and the command is asserted to carry the id, the name and the
reason — the three things an operator reads off either surface and acts on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from typer.testing import Result

from fantabot_app.api.tests.parity.conftest import SYNTHETIC_BASE, SeededWorld, cli_session

REASON = "left Serie A 2026-08-30 (Galatasaray)"
SOURCE = "goal.com"


def _first_seeded_player(world: SeededWorld) -> int:
    """A real `players` row, so the name join has something to resolve."""
    return int(world.player_ids[0])


def test_both_surfaces_list_the_same_rows(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """One implementation, two printers. What can still diverge is the wiring: a field
    dropped in the endpoint's mapping, or a name join done on only one side."""
    from fantabot.application.exclusions import read_exclusions, record_exclusion

    player_id = _first_seeded_player(seeded_db)
    with cli_session() as session:
        record_exclusion(session, player_id, reason=REASON, source=SOURCE)
        session.commit()

    with cli_session() as session:
        expected = read_exclusions(session)
    body = api.get("/api/v1/db/exclusions").json()
    output: Result = cli("db", "exclusions")

    assert body["error"] is None
    assert [row["player_id"] for row in body["exclusions"]] == [
        row.player_id for row in expected
    ]
    assert [row["nome"] for row in body["exclusions"]] == [row.nome for row in expected]
    assert [row["reason"] for row in body["exclusions"]] == [row.reason for row in expected]

    # The command's three load-bearing strings, not its layout.
    assert str(player_id) in output.output
    assert REASON in output.output
    assert SOURCE in output.output


def test_the_name_is_resolved_on_both_surfaces(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """The join used to be raw SQL inside the Typer body, where the page could not reach
    it — so the page would have had to write a second one or show a bare id. `1234` and
    `1234 Por Uno` answer different questions six months later."""
    from fantabot.application.exclusions import record_exclusion

    player_id = _first_seeded_player(seeded_db)
    expected_name = "Por Uno"  # `_POOL[0]`, and the seed is what makes it assertable
    with cli_session() as session:
        record_exclusion(session, player_id, reason=REASON, source="")
        session.commit()

    body = api.get("/api/v1/db/exclusions").json()
    output: Result = cli("db", "exclusions")

    (row,) = [r for r in body["exclusions"] if r["player_id"] == player_id]
    assert row["nome"] == expected_name
    assert expected_name in output.output


def test_recording_from_the_page_is_visible_to_the_command(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """The direction that matters: the operator who excludes a player in the browser at
    21:00 is the one who runs `asta bid` in a terminal at 21:05."""
    player_id = _first_seeded_player(seeded_db)

    response = api.post(
        "/api/v1/db/exclusions",
        json={"player_id": player_id, "reason": REASON, "source": SOURCE},
    )

    assert response.status_code == 201, response.text
    assert response.json()["recorded"]["nome"] == "Por Uno"

    output: Result = cli("db", "exclusions")
    assert str(player_id) in output.output
    assert REASON in output.output


def test_a_page_exclusion_reaches_the_planner_the_command_uses(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """The effect, not just the record.

    `excluded_player_ids` is read on every planning run, and `build_plan_inputs` **drops**
    those ids before anything is derived rather than filtering afterwards. This is the
    assertion that a row written through the browser lands in the set that read consults —
    which is the whole reason the table exists, and the one thing no list can show.
    """
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    player_id = _first_seeded_player(seeded_db)
    api.post("/api/v1/db/exclusions", json={"player_id": player_id, "reason": REASON})

    with cli_session() as session:
        assert str(player_id) in ReferenceRepository(session).excluded_player_ids()


@pytest.mark.parametrize(
    ("player_id", "reason"),
    [
        (SYNTHETIC_BASE, ""),
        (SYNTHETIC_BASE, "   "),
        (0, "left Serie A"),
        (-1, "left Serie A"),
    ],
)
def test_what_one_surface_refuses_the_other_refuses(
    seeded_db: SeededWorld,
    api: TestClient,
    cli: Callable[..., Result],
    player_id: int,
    reason: str,
) -> None:
    """Neither surface decides this: `clean_exclusion` does, and both call it.

    `reason: str = typer.Option(...)` refuses a *missing* reason and accepts
    `--reason ""`, and a JSON body has no Typer at all — so before the lift the command
    and the form would each have needed their own copy, and the two would have had to be
    kept in step by hand.
    """
    response = api.post(
        "/api/v1/db/exclusions", json={"player_id": player_id, "reason": reason}
    )
    result: Result = cli(
        "db", "exclude", "--player", str(player_id), "--reason", reason, expect_exit=2
    )

    assert response.status_code == 422, response.text
    assert result.exit_code == 2
    # The same sentence on both, because the endpoint carries the refusal rather than
    # restating it. The operator reading it on a page and in a terminal is one person.
    assert response.json()["detail"].split(".")[0] in result.output


def test_a_refusal_writes_nothing_on_either_surface(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """Fail closed, and leave the table where it was. `get_session` commits on clean
    exit, so a refusal that reached the upsert would be committed by the context
    manager on its way out."""
    from fantabot.application.exclusions import read_exclusions

    with cli_session() as session:
        before = read_exclusions(session)

    api.post("/api/v1/db/exclusions", json={"player_id": 0, "reason": "nope"})
    cli("db", "exclude", "--player", "0", "--reason", "nope", expect_exit=2)

    with cli_session() as session:
        assert read_exclusions(session) == before


def test_removing_from_the_page_takes_the_player_out_of_the_set_the_planner_reads(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """The effect of a removal, not just its record — the mirror of the write above.

    `excluded_player_ids` is what `build_plan_inputs` drops before anything is derived,
    so a removal that updated the list and not that set would leave the player out of
    every plan while the only screen that shows exclusions says he is back in.
    """
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    player_id = _first_seeded_player(seeded_db)
    api.post(
        "/api/v1/db/exclusions",
        json={"player_id": player_id, "reason": REASON, "source": SOURCE},
    )

    response = api.delete(f"/api/v1/db/exclusions/{player_id}")

    assert response.status_code == 200, response.text
    assert response.json()["removed"]["reason"] == REASON
    with cli_session() as session:
        assert str(player_id) not in ReferenceRepository(session).excluded_player_ids()
    assert str(player_id) not in cli("db", "exclusions").output


def test_removing_from_the_terminal_is_visible_to_the_page(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """The other direction, and the one an operator actually takes at 21:05: the row
    was a typo, the terminal is already open, and the browser must not go on showing a
    player as unbuyable."""
    from fantabot.application.exclusions import record_exclusion

    player_id = _first_seeded_player(seeded_db)
    with cli_session() as session:
        record_exclusion(session, player_id, reason=REASON, source=SOURCE)
        session.commit()

    cli("db", "unexclude", "--player", str(player_id))

    body = api.get("/api/v1/db/exclusions").json()
    assert body["error"] is None
    assert player_id not in [row["player_id"] for row in body["exclusions"]]


def test_a_removal_that_matched_nothing_is_refused_on_both_surfaces(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """Neither surface decides this either. The codes differ because the protocols do —
    404 and exit 2 — but the sentence is one sentence, carried rather than restated."""
    absent = SYNTHETIC_BASE + 7

    response = api.delete(f"/api/v1/db/exclusions/{absent}")
    result: Result = cli("db", "unexclude", "--player", str(absent), expect_exit=2)

    assert response.status_code == 404, response.text
    assert response.json()["detail"].split(".")[0] in result.output


def test_a_refused_removal_removes_nothing_on_either_surface(
    seeded_db: SeededWorld, api: TestClient, cli: Callable[..., Result]
) -> None:
    """Fail closed, and leave the table where it was — `get_session` commits on clean
    exit, so a refusal that reached the delete would be committed on its way out."""
    from fantabot.application.exclusions import read_exclusions, record_exclusion

    player_id = _first_seeded_player(seeded_db)
    with cli_session() as session:
        record_exclusion(session, player_id, reason=REASON, source=SOURCE)
        session.commit()
    with cli_session() as session:
        before = read_exclusions(session)

    api.delete(f"/api/v1/db/exclusions/{SYNTHETIC_BASE + 8}")
    cli("db", "unexclude", "--player", str(SYNTHETIC_BASE + 8), expect_exit=2)

    with cli_session() as session:
        assert read_exclusions(session) == before

"""`lega show` and `GET /lega`, over one snapshot capture.

`api/reads/league.py` hand-wrote SQLAlchemy over the same snapshot models `lega show`
hand-wrote it over, in two places, with no shared function. The app's existed for a real
reason — `LeagueRepository` is write-only, because the snapshot tables are append-only and
the point of them is the drift — but that is a reason for a read helper, not for a *second*
one.

**Both surfaces now call `application/lega_reads.py`**, and the file is deleted. The two
still *report* different things — `lega show` is a capture inventory, `/lega` is the
settings the newest capture carries — which is fine and is the point: two projections of
one read, rather than two reads that can disagree about which capture is the newest.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient
from typer.testing import Result

from .conftest import SeededWorld


def test_the_endpoint_reports_the_capture_that_was_seeded(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    body = api.get("/api/v1/lega").json()
    ours = [row for row in body if row["league_id"] == seeded_db.league_id]

    assert len(ours) == 1, f"expected exactly one overview for the seeded lega, got {ours}"
    (overview,) = ours
    assert overview["budget"] == seeded_db.budget
    assert overview["roster_size"] == seeded_db.roster_size
    assert overview["min_roles"] == list(seeded_db.min_roles)
    assert overview["team_count"] == 1


def test_the_rosters_endpoint_zips_the_two_parallel_arrays(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """`roster_ids` and `roster_costs` are two arrays on one row; a reader that lost the
    pairing would report a rosa nobody bought."""
    (team,) = api.get(f"/api/v1/lega/{seeded_db.league_id}/rosters").json()

    assert team["credits_spent"] == 100
    assert [slot["player_id"] for slot in team["roster"]] == [
        int(pid) for pid in seeded_db.player_ids[:3]
    ]
    assert [slot["cost"] for slot in team["roster"]] == [10, 20, 70]


def test_the_command_reaches_the_same_capture(
    seeded_db: SeededWorld, cli: Callable[..., Result]
) -> None:
    """Not a text diff — a Rich table compared against JSON fails on a column width and
    gets deleted. What is asserted is that the command ran against *this* database and
    found the lega, rather than reporting it unreachable and exiting 1."""
    result = cli("lega", "show", "--league", str(seeded_db.league_id))

    assert "database unreachable" not in result.output
    assert str(seeded_db.league_id) in result.output


def test_both_surfaces_read_the_same_capture_through_the_same_function(
    seeded_db: SeededWorld, api: TestClient
) -> None:
    """One implementation, two projections. The endpoint's `team_count` and the command's
    `league_team_snapshot` row count are the same number computed once."""
    from fantabot.application.lega_reads import capture_inventory

    from .conftest import cli_session

    with cli_session() as session:
        inventory = {row.table: row for row in capture_inventory(session, seeded_db.league_id)}

    body = api.get("/api/v1/lega").json()
    (overview,) = [row for row in body if row["league_id"] == seeded_db.league_id]

    assert inventory["league_team_snapshot"].rows == overview["team_count"]
    assert inventory["league_snapshot"].rows == 1
    assert inventory["league_snapshot"].captured_at is not None


def test_a_lega_with_no_capture_reads_as_never_and_not_as_zero(
    seeded_db: SeededWorld,
) -> None:
    """"Never captured" and "captured, zero rows" are different facts, and a count alone
    cannot tell them apart. `lega show` prints "mai" for the first."""
    from fantabot.application.lega_reads import capture_inventory

    from .conftest import cli_session

    with cli_session() as session:
        rows = {row.table: row for row in capture_inventory(session, 424_242)}

    assert rows["league_snapshot"].captured_at is None
    assert rows["league_snapshot"].rows == 0

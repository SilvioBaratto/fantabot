"""`lega show` and `GET /lega`, over one snapshot capture.

`api/reads/league.py` hand-writes SQLAlchemy over the same snapshot models `lega show`
hand-writes it over, in two places, with no shared function. The app's exists because
`LeagueRepository` is write-only (the snapshot tables are append-only, so the drift stays
visible) — a real reason for a read helper, and not a reason for a *second* one.

**The two do not answer the same question today, and that is the finding.** `lega show`
reports a capture inventory — per table, when it was last captured and how many rows that
capture holds — while `/lega` reports the settings the newest capture carries. So what can
be compared before 1.10 lifts the query is that both reach the same capture: the endpoint's
numbers are the seed's, and the command succeeds against the same database rather than
reporting it unreachable. 1.10 makes it one function and this file compares that.
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

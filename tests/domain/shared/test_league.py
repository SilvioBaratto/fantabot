"""`parse_team_snapshot`: the `teams/my` body, translated into named fields. Pure."""

from __future__ import annotations

from fantabot.domain.shared.league import TeamSnapshot, parse_team_snapshot

#: A real `teams/my` body, trimmed to the fields `parse_team_snapshot` reads.
BODY = {
    "id": 10000003,
    "idu": 20000003,
    "cri": 500,
    "crs": 474,
    "cr": 26,
    "n": "Team C",
    "nu": "Owner C",
    "cal": "7612;7600",
    "cs": "1;1",
}


def test_the_abbreviated_keys_land_on_named_fields() -> None:
    snapshot = parse_team_snapshot(4103937, BODY)

    assert snapshot == TeamSnapshot(
        league_id=4103937,
        team_id=10000003,
        user_id=20000003,
        nome="Team C",
        owner="Owner C",
        credits_initial=500,
        credits_spent=474,
        credits_remaining=26,
    )


def test_the_league_id_comes_from_the_caller_not_the_body() -> None:
    """The body carries no league id of its own — only the caller knows which
    league it asked, since the same team id could exist in another lega."""
    snapshot = parse_team_snapshot(999, BODY)

    assert snapshot.league_id == 999


def test_a_missing_user_id_stays_none_not_zero() -> None:
    snapshot = parse_team_snapshot(4103937, {**BODY, "idu": None})

    assert snapshot.user_id is None

"""`latest_settings` must not let a partial sync's empty snapshot shadow a complete one.

`lega sync` appends a snapshot even when `settings/rosters` failed, with roster_size and
role_groups NULL. Ordering by `captured_at` alone returned that row, and `rules_for_lega`
then planned a 25/32 lega as the size-30 default.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fantabot.application.lega_reads import latest_settings, rules_for_league
from fantabot.domain.asta import state
from fantabot.domain.asta.state import RosterRules

LEGA = 4103937
# Arrays stay NULL: SQLite has no ARRAY type, and the columns under test are scalar.
DDL = """
CREATE TABLE league_snapshot (
    captured_at TIMESTAMP NOT NULL, league_id BIGINT NOT NULL,
    competition_id BIGINT, matchday SMALLINT, budget SMALLINT,
    roster_size SMALLINT, role_groups SMALLINT,
    min_roles TEXT, max_roles TEXT, modules TEXT, bench_size SMALLINT,
    PRIMARY KEY (captured_at, league_id)
)
"""


def _session(rows: list[tuple[int, int, int | None, int | None]]) -> Session:
    """`rows` are `(day, league_id, roster_size, role_groups)`."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(DDL))
        for day, league, size, groups in rows:
            conn.execute(
                text(
                    "INSERT INTO league_snapshot (captured_at, league_id, matchday,"
                    " roster_size, role_groups) VALUES (:t, :l, :m, :s, :g)"
                ),
                {
                    "t": datetime(2026, 9, day, tzinfo=UTC).replace(tzinfo=None),
                    "l": league,
                    "m": day,
                    "s": size,
                    "g": groups,
                },
            )
    return Session(engine)


def test_an_empty_newest_snapshot_does_not_shadow_a_complete_one() -> None:
    with _session([(5, LEGA, 32, 2), (23, LEGA, None, None)]) as s:
        snap = latest_settings(s, LEGA)
    assert snap is not None
    assert (snap.roster_size, snap.role_groups) == (32, 2)


def test_the_newest_complete_snapshot_wins_among_complete_ones() -> None:
    with _session([(5, LEGA, 30, 2), (11, LEGA, 32, 2), (23, LEGA, None, None)]) as s:
        snap = latest_settings(s, LEGA)
    assert snap is not None
    assert snap.roster_size == 32


def test_a_lega_with_only_empty_snapshots_still_answers_with_the_newest() -> None:
    with _session([(5, LEGA, None, None), (23, LEGA, None, None)]) as s:
        snap = latest_settings(s, LEGA)
    assert snap is not None
    assert snap.matchday == 23


def test_other_legas_complete_rows_are_not_borrowed() -> None:
    with _session([(5, 999, 25, 1), (23, LEGA, None, None)]) as s:
        snap = latest_settings(s, LEGA)
    assert snap is not None
    assert snap.league_id == LEGA


def test_no_snapshot_is_none() -> None:
    with _session([]) as s:
        assert latest_settings(s, LEGA) is None


@pytest.mark.parametrize("groups, fmt", [(1, "classic"), (2, "mantra")])
def test_rules_are_derived_from_the_complete_snapshot_not_the_default(
    monkeypatch: pytest.MonkeyPatch, groups: int, fmt: str
) -> None:
    seen: dict[str, object] = {}

    def spy(**kwargs: object) -> tuple[RosterRules, str]:
        seen.update(kwargs)
        return RosterRules(), "spy"

    monkeypatch.setattr(state, "rules_for_lega", spy)
    with _session([(5, LEGA, 25, groups), (23, LEGA, None, None)]) as s:
        _, _, got = rules_for_league(s, LEGA)
    assert (seen["roster_size"], seen["role_groups"]) == (25, groups)
    assert got == fmt

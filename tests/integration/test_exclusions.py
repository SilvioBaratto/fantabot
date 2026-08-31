"""The exclusion table, round-tripped. Marked ``db``.

The pure filter is covered in `tests/application/test_asta_planner.py`; this covers the
half that has to survive a re-scrape, which is the whole reason it is a table rather than
a flag. `fantacalcio.it` still listed Rafael Leao at MIL the day after his permanent
transfer to Galatasaray, so the exclusion has to outlive the next `db scrape quotazioni`.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

pytestmark = pytest.mark.db


def test_an_excluded_player_is_read_back_as_a_string_id(db_session: Session) -> None:
    """Every id in the planning layer is a `str`; the column is a `BigInteger`."""
    repo = ReferenceRepository(db_session)
    repo.exclude_player(999_001, reason="test", source="pytest")

    assert "999001" in repo.excluded_player_ids()


def test_recording_the_same_player_twice_replaces_the_reason(db_session: Session) -> None:
    """An upsert, like every other write here — re-running a correction is not an error."""
    repo = ReferenceRepository(db_session)
    repo.exclude_player(999_002, reason="first guess", source="a")
    repo.exclude_player(999_002, reason="what actually happened", source="b")

    assert [(pid, r, s) for pid, r, s in repo.exclusions() if pid == 999_002] == [
        (999_002, "what actually happened", "b")
    ]


def test_the_reason_and_source_come_back_for_a_human(db_session: Session) -> None:
    """An exclusion with no provenance is indistinguishable from a typo, and this one
    removes a player from every plan the bot makes."""
    repo = ReferenceRepository(db_session)
    repo.exclude_player(999_003, reason="left Serie A 2026-08-30", source="goal.com")

    assert (999_003, "left Serie A 2026-08-30", "goal.com") in repo.exclusions()


def test_the_id_set_is_exactly_what_the_table_holds(db_session: Session) -> None:
    """The two readers cannot disagree: one gates the pool, the other explains it.

    This was `== set()` and asserted an empty table, which stopped being true the moment
    the first real exclusion was recorded -- a test about the *default* state, written
    where the state is shared and durable. It compares the two reads instead, which holds
    whatever the table contains.
    """
    repo = ReferenceRepository(db_session)
    repo.exclude_player(999_004, reason="test", source="pytest")

    assert repo.excluded_player_ids() == {str(pid) for pid, _, _ in repo.exclusions()}
    assert "999004" in repo.excluded_player_ids()

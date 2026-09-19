"""The exclusion table, round-tripped. Marked ``db``.

The pure filter is covered in `tests/application/test_asta_planner.py`; this covers the
half that has to survive a re-scrape, which is the whole reason it is a table rather than
a flag. `fantacalcio.it` still listed Rafael Leao at MIL the day after his permanent
transfer to Galatasaray, so the exclusion has to outlive the next `db scrape quotazioni`.
"""

from __future__ import annotations

import pytest
from conftest import make_synthetic_players
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.reference import ReferenceRepository
from fantabot.application.exclusions import (
    ExclusionNotFound,
    InvalidExclusion,
    record_exclusion,
    remove_exclusion,
)

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


def test_player_names_resolves_only_the_ids_the_table_carries(db_session: Session) -> None:
    """The join `db exclusions` used to run as raw SQL inside the Typer body.

    An id `players` does not carry is simply absent — never a placeholder — because
    whether the id resolves at all is the fact that tells a typo from a season nobody
    has scraped.
    """
    (seeded,) = make_synthetic_players(db_session, 1)
    player_id = int(seeded)

    names = ReferenceRepository(db_session).player_names([player_id, 999_999_999])

    assert names == {player_id: f"synthetic-{seeded}"}


def test_player_names_runs_no_query_for_an_empty_list(db_session: Session) -> None:
    """`IN ()` is not valid SQL, and the list is empty on every fresh install."""
    assert ReferenceRepository(db_session).player_names([]) == {}


def test_recording_one_reads_the_list_back_with_the_name_attached(
    db_session: Session,
) -> None:
    """`record_exclusion`'s whole shape: validate, upsert, read back.

    Read back rather than assembled from the arguments, so the confirmation an operator
    sees is what the table now holds.
    """
    (seeded,) = make_synthetic_players(db_session, 1)
    player_id = int(seeded)

    recorded = record_exclusion(
        db_session, player_id, reason="  left Serie A 2026-08-30\n", source=" goal.com "
    )

    assert recorded.row.player_id == player_id
    assert recorded.row.nome == f"synthetic-{seeded}"
    assert recorded.row.reason == "left Serie A 2026-08-30"  # trimmed on the way in
    assert recorded.row.source == "goal.com"
    assert recorded.total == len(recorded.exclusions)
    assert recorded.row in recorded.exclusions


def test_a_refused_exclusion_writes_nothing(db_session: Session) -> None:
    """The refusal runs before the upsert, so a blank reason leaves the table alone."""
    before = ReferenceRepository(db_session).exclusions()

    with pytest.raises(InvalidExclusion):
        record_exclusion(db_session, 999_005, reason="   ", source="")

    assert ReferenceRepository(db_session).exclusions() == before


def test_an_exclusion_for_an_unscraped_id_still_lists_with_no_name(
    db_session: Session,
) -> None:
    """Flagged by the surfaces, not refused here: the id may belong to a season nobody
    has scraped yet, and the row is what the operator asked for."""
    recorded = record_exclusion(db_session, 999_006, reason="a guess", source="")

    assert recorded.row.nome is None
    assert recorded.row.player_id == 999_006


def test_removing_one_takes_it_out_of_both_reads(db_session: Session) -> None:
    """The gate and the list are two reads over one table, so a removal that left
    either behind would be a player still kept out of every plan by a row the list no
    longer shows."""
    repo = ReferenceRepository(db_session)
    repo.exclude_player(999_007, reason="a typo", source="")

    remove_exclusion(db_session, 999_007)

    assert "999007" not in repo.excluded_player_ids()
    assert 999_007 not in [pid for pid, _, _ in repo.exclusions()]


def test_the_removed_row_comes_back_whole(db_session: Session) -> None:
    """With its name and its reason: the reason is the only half of the row nothing
    else holds, and printing it is what makes the removal undoable by hand."""
    (seeded,) = make_synthetic_players(db_session, 1)
    player_id = int(seeded)
    record_exclusion(db_session, player_id, reason="left Serie A 2026-08-30", source="goal.com")

    removed = remove_exclusion(db_session, player_id)

    assert removed.row.player_id == player_id
    assert removed.row.nome == f"synthetic-{seeded}"
    assert removed.row.reason == "left Serie A 2026-08-30"
    assert removed.row.source == "goal.com"
    assert removed.row not in removed.exclusions
    assert removed.total == len(removed.exclusions)


def test_removing_what_is_not_there_raises_and_writes_nothing(db_session: Session) -> None:
    """A delete matching no row is not a removal, and the operator's next act depends
    on which of the two happened."""
    before = ReferenceRepository(db_session).exclusions()

    with pytest.raises(ExclusionNotFound) as caught:
        remove_exclusion(db_session, 999_008)

    assert "999008" in str(caught.value)
    assert ReferenceRepository(db_session).exclusions() == before


def test_an_id_that_could_never_be_recorded_today_can_still_be_removed(
    db_session: Session,
) -> None:
    """`clean_exclusion` refuses a non-positive id, and `db exclude` had no validation
    at all before T24 — so a `0` is exactly the row this command is the remedy for.
    Validating the id here would make the unreachable row permanent."""
    ReferenceRepository(db_session).exclude_player(0, reason="written before T24", source="")

    with pytest.raises(InvalidExclusion):
        record_exclusion(db_session, 0, reason="still refused", source="")
    removed = remove_exclusion(db_session, 0)

    assert removed.row.player_id == 0
    assert 0 not in [pid for pid, _, _ in ReferenceRepository(db_session).exclusions()]

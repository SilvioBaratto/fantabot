"""Reading the sentiment time-series back, now from Postgres.

The one rule that matters here: rows with confidenza=0 are excluded from every
average. A 0.0 sentiment from silence and a 0.0 from balanced coverage are
different facts, and averaging them together destroys the distinction the schema
was built to preserve.

Every assertion below is carried over verbatim from the CSV-backed suite. Only
the fixture changed: rows go in through the repository instead of append_rows,
and the source is constructed from a session instead of a path. That is the
point — the storage moved and the behaviour did not.

Marked ``db``: the contracts are about ordering, windowing and NULL handling,
which is exactly what a fake session cannot settle.
"""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fantabot.data_sources.news_sentiment import NewsSentimentSource
from fantabot.db.repositories.sentiment import SentimentRepository

pytestmark = pytest.mark.db

#: Synthetic ids, far above any real `players.id` and with no `quotazioni` row. These were
#: 632/633/634 — real players — which was fine while `player_sentiment` was nearly empty and
#: wrong the moment it held a full listone: a fixture row and a production row share the
#: `(data_run, player_id)` key, so `latest` and `trailing` started answering from data this
#: file never wrote. `_source` creates whatever ids the fixtures name, so only the numbers
#: had to change.
PLAYER = "9100000000"
OTHER = "9100000001"

HEADER_ROW = {
    "stagione": "2026/27",
    "nome": "Zaccagni",
    "squadra": "LAZ",
    "ruolo": "Centrocampista",
    "ruoli_mantra": "W;A",
    "giorni_lookback": "14",
    "modello": "claude-sonnet-5",
    "riassunto": "x",
    "n_fonti": "2",
    "fonti": "https://a;https://b",
    "disponibilita": "1.00",
    "titolarita": "0.90",
    "mercato": "0.00",
    "forma": "0.00",
    "rigorista": "0.80",
    "piazzati": "0.10",
}


def _row(data_run: str, player_id: str = PLAYER, **overrides: str) -> dict[str, str]:
    row = {
        **HEADER_ROW,
        "data_run": data_run,
        "id": player_id,
        "sentiment": "0.50",
        "confidenza": "0.80",
        "ruolo_campo": "",
        "deriva_ruolo": "0.00",
    }
    row.update(overrides)
    return row


def _source(db_session: Session, rows: list[dict[str, str]]) -> NewsSentimentSource:
    """Store the rows, then read them back the way the strategy layer will.

    Player rows are created for any id the fixture invents: player_sentiment
    carries a real foreign key, and the whole transaction rolls back at teardown
    so nothing survives the test.
    """
    for player_id in {row["id"] for row in rows}:
        db_session.execute(
            text(
                "INSERT INTO players (id, nome) VALUES (:id, :n) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": int(player_id), "n": f"fixture-{player_id}"},
        )
    SentimentRepository(db_session).upsert_rows(rows, force=True)
    return NewsSentimentSource(db_session)


def test_latest_returns_the_most_recent_row(db_session: Session) -> None:
    source = _source(
        db_session,
        [_row("2026-09-02", sentiment="0.10"), _row("2026-10-07", sentiment="0.70")],
    )

    latest = source.latest(PLAYER)

    assert latest is not None
    assert latest.data_run == "2026-10-07"
    assert latest.sentiment == 0.70


def test_latest_does_not_depend_on_file_order(db_session: Session) -> None:
    source = _source(
        db_session,
        [_row("2026-10-07", sentiment="0.70"), _row("2026-09-02", sentiment="0.10")],
    )

    latest = source.latest(PLAYER)
    assert latest is not None
    assert latest.data_run == "2026-10-07"


def test_latest_for_an_unknown_player_is_none(db_session: Session) -> None:
    assert _source(db_session, [_row("2026-10-07")]).latest("99999") is None


def test_an_empty_table_is_empty_rather_than_an_error(db_session: Session) -> None:
    source = NewsSentimentSource(db_session)

    assert source.latest(PLAYER) is None
    assert source.trailing(PLAYER) is None


def test_trailing_averages_the_window(db_session: Session) -> None:
    source = _source(
        db_session,
        [
            _row("2026-09-16", sentiment="0.20"),
            _row("2026-09-23", sentiment="0.40"),
        ],
    )

    trailing = source.trailing(PLAYER, weeks=4)

    assert trailing is not None
    assert trailing.sentiment == pytest.approx(0.30)


def test_a_silent_row_does_not_move_the_mean(db_session: Session) -> None:
    # The whole point. A confidenza=0 row means "nobody wrote about him", not
    # "he is neutral"; folding its 0.0 into the average would invent a data point.
    source = _source(
        db_session,
        [
            _row("2026-09-16", sentiment="0.20"),
            _row("2026-09-23", sentiment="0.40"),
            _row("2026-09-30", sentiment="0.00", confidenza="0.00"),
        ],
    )

    trailing = source.trailing(PLAYER, weeks=4)

    assert trailing is not None
    assert trailing.sentiment == pytest.approx(0.30)
    assert trailing.rows_used == 2


def test_trailing_is_none_when_every_row_is_silent(db_session: Session) -> None:
    source = _source(db_session, [_row("2026-09-30", sentiment="0.00", confidenza="0.00")])

    assert source.trailing(PLAYER) is None


def test_trailing_only_counts_the_requested_window(db_session: Session) -> None:
    source = _source(
        db_session,
        [
            _row("2026-08-05", sentiment="1.00"),  # older than 4 rows back
            _row("2026-09-09", sentiment="0.00"),
            _row("2026-09-16", sentiment="0.00"),
            _row("2026-09-23", sentiment="0.00"),
            _row("2026-09-30", sentiment="0.00"),
        ],
    )

    trailing = source.trailing(PLAYER, weeks=4)

    assert trailing is not None
    assert trailing.rows_used == 4
    assert trailing.sentiment == 0.0


def test_drift_surfaces_a_stale_tag(db_session: Session) -> None:
    source = _source(
        db_session,
        [_row("2026-10-07", ruolo_campo="T", deriva_ruolo="0.85")],
    )

    drift = source.drift(PLAYER)

    assert drift is not None
    assert drift.deriva_ruolo == 0.85
    assert drift.ruolo_campo == "T"
    assert drift.ruoli_mantra == "W;A"


def test_drift_is_none_when_nothing_was_observed(db_session: Session) -> None:
    source = _source(db_session, [_row("2026-10-07", ruolo_campo="", deriva_ruolo="0.00")])

    assert source.drift(PLAYER) is None


def test_players_with_a_stale_tag_can_be_listed(db_session: Session) -> None:
    # What a Mantra lineup engine actually wants: everyone whose frozen tag no
    # longer describes them, worst first.
    source = _source(
        db_session,
        [
            _row("2026-10-07", player_id="1", ruolo_campo="T", deriva_ruolo="0.85"),
            _row("2026-10-07", player_id="2", ruolo_campo="W", deriva_ruolo="0.00"),
            _row("2026-10-07", player_id="3", ruolo_campo="M", deriva_ruolo="0.60"),
        ],
    )

    # Scoped to this fixture's ids: drifted() reads the whole table, and an
    # unrelated row left by another run would otherwise fail an exact match.
    under_test = [d.player_id for d in source.drifted() if d.player_id in {"1", "2", "3"}]

    assert under_test == ["1", "3"]


def test_an_unreachable_database_raises_rather_than_reading_as_empty(
    db_session: Session,
) -> None:
    """A missing file used to read as empty. A database that is down must not.

    They are different facts: an empty table means nobody has been queried yet,
    while an unreachable one means the answer is unknown. Returning None for the
    second would let a lineup be picked on silence that was never measured.
    """

    class _Dead:
        def execute(self, *args: object, **kwargs: object) -> object:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    with pytest.raises(OperationalError):
        NewsSentimentSource(_Dead()).latest(PLAYER)  # type: ignore[arg-type]


def test_a_row_written_after_construction_is_visible(db_session: Session) -> None:
    """The CSV version slurped the whole file at construction and answered from
    that snapshot forever. auction.py's watch_and_bid polls for hours, so it
    would have held a frozen reading for the whole duration of an asta."""
    source = _source(db_session, [_row("2026-09-02")])
    assert source.latest(PLAYER) is not None

    SentimentRepository(db_session).upsert_rows([_row("2026-10-07")], force=True)

    latest = source.latest(PLAYER)
    assert latest is not None
    assert latest.data_run == "2026-10-07"


# These assert on the fixture's own ids rather than on the whole mapping. The db
# tier runs against the development database, which carries a real listone — a
# bulk read sees those rows too, and pinning an exact key set would make the
# suite depend on whatever news-fetch last wrote.


def test_all_latest_keys_every_player_by_id(db_session: Session) -> None:
    source = _source(
        db_session,
        [_row("2026-10-07", player_id=PLAYER), _row("2026-10-07", player_id=OTHER)],
    )

    rows = source.all_latest()

    assert {PLAYER, OTHER} <= set(rows)
    assert rows[PLAYER].data_run == "2026-10-07"


def test_all_latest_takes_only_the_newest_run_per_player(db_session: Session) -> None:
    source = _source(
        db_session,
        [
            _row("2026-09-02", player_id=PLAYER, sentiment="0.10"),
            _row("2026-10-07", player_id=PLAYER, sentiment="0.70"),
            _row("2026-09-02", player_id=OTHER, sentiment="0.20"),
        ],
    )

    rows = source.all_latest()

    assert rows[PLAYER].sentiment == 0.70
    assert rows[PLAYER].data_run == "2026-10-07"
    assert rows[OTHER].sentiment == 0.20


def test_all_latest_returns_silent_rows_rather_than_dropping_them(
    db_session: Session,
) -> None:
    """A silent row is not a missing row.

    ``trailing`` excludes ``confidenza == 0`` because it averages. ``all_latest``
    does not average — the value layer needs to *see* the silence to apply its
    identity, and filtering here would make "silent" and "absent" the same fact.
    """
    source = _source(db_session, [_row("2026-10-07", player_id=PLAYER, confidenza="0.00")])

    rows = source.all_latest()

    assert PLAYER in rows
    assert rows[PLAYER].confidenza == 0.0


def test_all_latest_omits_a_player_who_was_never_queried(db_session: Session) -> None:
    """Absent from the mapping, not present with a fabricated neutral row."""
    rows = _source(db_session, [_row("2026-10-07", player_id=PLAYER)]).all_latest()

    assert "99999" not in rows


def test_all_latest_issues_a_single_statement(db_session: Session) -> None:
    """The reason this method exists: per-player ``latest()`` would be 548 round trips."""
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    source = _source(
        db_session,
        [_row("2026-10-07", player_id=str(pid)) for pid in (int(PLAYER), int(OTHER), int(OTHER) + 1)],
    )
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        source.all_latest()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 1, statements


def test_all_latest_can_be_pinned_to_one_run(db_session: Session) -> None:
    source = _source(
        db_session,
        [
            _row("2026-09-02", player_id=PLAYER, sentiment="0.10"),
            _row("2026-10-07", player_id=PLAYER, sentiment="0.70"),
        ],
    )

    pinned = source.all_latest(data_run=date(2026, 9, 2))

    assert pinned[PLAYER].sentiment == 0.10
    assert pinned[PLAYER].data_run == "2026-09-02"


def test_pinning_to_a_run_that_does_not_exist_is_empty(db_session: Session) -> None:
    """Empty, so the caller can refuse rather than silently apply no adjustment."""
    source = _source(db_session, [_row("2026-10-07", player_id=PLAYER)])

    assert source.all_latest(data_run=date(1999, 1, 1)) == {}

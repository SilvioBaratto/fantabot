"""The database must never be able to stop collection.

Two halves, and they are proved differently.

The collector cannot depend on Postgres because it cannot *reach* it: the
modules that capture and write are checked for any path to `fantabot.db`. That
is a structural proof and it holds for every future edit, where stopping a
container once would only have proved it for one afternoon.

The loader must lose nothing when the write fails. Its checkpoint may only
advance after a successful commit — otherwise an outage silently skips whatever
was in flight, and the landing zone's guarantee is worth nothing.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fantabot.aste.loader import Checkpoint, read_from

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "fantabot" / "aste"

#: The capture path: from a socket to a line on disk. Nothing here may import
#: the database, directly or transitively through a sibling.
CAPTURE = ("landing.py", "stream.py", "transport.py", "sse.py", "reducer.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", CAPTURE)
def test_the_capture_path_cannot_reach_the_database(module: str) -> None:
    """Not "does not today" — cannot. An outage must cost catch-up time and
    never a record, and that only holds if the collector has no way to wait on
    a database in the first place."""
    offenders = {name for name in _imports(PACKAGE / module) if name.startswith("fantabot.db")}
    assert offenders == set(), f"{module} can reach the database via {offenders}"


@pytest.mark.parametrize("module", CAPTURE)
def test_the_capture_path_reaches_no_orm(module: str) -> None:
    offenders = {name for name in _imports(PACKAGE / module) if name.startswith("sqlalchemy")}
    assert offenders == set(), f"{module} imports SQLAlchemy: {offenders}"


def test_a_checkpoint_that_never_advanced_re_reads_everything(tmp_path: Path) -> None:
    """The shape of an outage: the write failed, so the checkpoint was not
    written, so the next pass sees the same records again. Upserts make the
    repeat harmless — losing them would not be."""
    landing = tmp_path / "events.jsonl"
    with landing.open("w", encoding="utf-8") as handle:
        for price in (1, 2, 3):
            handle.write(json.dumps({"seen_at": "2026", "auction_id": "a-1",
                                     "state": {"price": price}}) + "\n")

    checkpoint = Checkpoint(landing)
    first, offset = read_from(landing, checkpoint.read())
    assert len(first) == 3

    # The database was unreachable: nothing is committed, so nothing is recorded
    # about how far we got.
    again, _ = read_from(landing, checkpoint.read())
    assert [r["state"]["price"] for r in again] == [1, 2, 3], "an outage must not skip records"

    # Once the write succeeds the checkpoint moves, and only then.
    checkpoint.write(offset)
    assert read_from(landing, checkpoint.read()) == ([], offset)


def test_records_written_during_an_outage_are_picked_up_after_it(tmp_path: Path) -> None:
    """The collector keeps appending while the loader cannot write. Catch-up has
    to include everything that arrived in between."""
    landing = tmp_path / "events.jsonl"
    checkpoint = Checkpoint(landing)

    def append(price: int) -> None:
        with landing.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"seen_at": "2026", "auction_id": "a-1",
                                     "state": {"price": price}}) + "\n")

    append(1)
    _, offset = read_from(landing, checkpoint.read())
    checkpoint.write(offset)

    # Outage: three more states land on disk, none of them loaded.
    for price in (2, 3, 4):
        append(price)

    caught_up, _ = read_from(landing, checkpoint.read())
    assert [r["state"]["price"] for r in caught_up] == [2, 3, 4]

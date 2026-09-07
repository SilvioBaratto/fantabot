"""Reading the journal back, and the schema drift that made it necessary.

The row schema was stated in five places and had already drifted. The writer emits
`bargain_spent`, `bargain_allowance` and `error`; the app's `JournalRow` read none of the
three — the bargain pair landed in `44cfe89`, an ancestor of the viewer commit `86acb6c`,
so the reader was written *after* those keys existed and dropped them anyway.

The drift is not cosmetic. `waiting` and `error` rows carry two or three keys, so a viewer
that knows only the decision fields renders both as **a row of nulls** — and telling a
skipped poll from a crash is `error_row`'s entire purpose.

So the field list, the skip-and-count rule, the 1-based index and newest-first are stated
once, here, beside the writer. `test_the_writer_and_the_reader_agree` is what keeps them
from drifting again: it reads the writer's own dict literals rather than trusting that
someone updated both ends.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest
from _paths import module_file

from fantabot.adapters.files.room_journal import (
    ROW_FIELDS,
    JournalEntry,
    RoomJournal,
    read_rows,
)


def write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_missing_journal_reads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    rows, skipped = read_rows(tmp_path / "nothing.jsonl")

    assert rows == ()
    assert skipped == 0


def test_rows_arrive_newest_first_and_keep_their_line_number(tmp_path: Path) -> None:
    """The audit that found the three bidder defects cites line numbers; paging must not
    cost a row its citation, and neither must reversing."""
    path = write(
        tmp_path / "j.jsonl",
        json.dumps({"at_ms": 1, "decision": "hold"}),
        json.dumps({"at_ms": 2, "decision": "bid"}),
        json.dumps({"at_ms": 3, "decision": "pass"}),
    )

    rows, _ = read_rows(path)

    assert [r.index for r in rows] == [3, 2, 1]
    assert [r.decision for r in rows] == ["pass", "bid", "hold"]


def test_a_torn_trailing_line_is_counted_and_skipped(tmp_path: Path) -> None:
    """The journal flushes per line, so the one line a crash can tear is the last — which
    is the newest, and therefore the first row a tail-first viewer would try to render."""
    path = tmp_path / "j.jsonl"
    path.write_text(
        json.dumps({"at_ms": 1, "decision": "hold"}) + "\n" + '{"at_ms": 2, "deci',
        encoding="utf-8",
    )

    rows, skipped = read_rows(path)

    assert skipped == 1
    assert [r.index for r in rows] == [1]


def test_a_json_line_that_is_not_an_object_is_skipped_too(tmp_path: Path) -> None:
    path = write(tmp_path / "j.jsonl", "[1, 2, 3]", json.dumps({"decision": "hold"}))

    rows, skipped = read_rows(path)

    assert skipped == 1
    assert len(rows) == 1


def test_a_blank_line_is_neither_a_row_nor_a_skip(tmp_path: Path) -> None:
    path = write(tmp_path / "j.jsonl", json.dumps({"decision": "hold"}), "", "   ")

    rows, skipped = read_rows(path)

    assert (len(rows), skipped) == (1, 0)


def test_a_journal_that_exists_and_cannot_be_read_is_not_an_empty_one(tmp_path: Path) -> None:
    """A directory where a file was expected. Rendering that as "no journal yet" would
    send the operator looking for a path that is already right, so it raises and the
    caller chooses the screen."""
    (tmp_path / "j.jsonl").mkdir()

    with pytest.raises(OSError):
        read_rows(tmp_path / "j.jsonl")


def test_the_waiting_and_error_rows_are_not_a_row_of_nulls(tmp_path: Path) -> None:
    """`error_row`'s whole purpose is telling a skipped poll from a crash."""
    path = write(
        tmp_path / "j.jsonl",
        json.dumps({"at_ms": 1, "decision": "waiting"}),
        json.dumps({"at_ms": 2, "decision": "error", "error": "TimeoutException"}),
    )

    rows, _ = read_rows(path)

    assert rows[0].decision == "error"
    assert rows[0].error == "TimeoutException"
    assert rows[1].decision == "waiting"
    assert rows[1].error is None


def test_the_bargain_pair_survives_the_read(tmp_path: Path) -> None:
    """The two keys the app's reader dropped. An aggregate cap nobody can see after the
    evening is one the operator only finds out about by not understanding a held bid."""
    path = write(
        tmp_path / "j.jsonl",
        json.dumps({"decision": "bid", "bargain_spent": 37, "bargain_allowance": 50}),
    )

    (row,) = read_rows(path)[0]

    assert (row.bargain_spent, row.bargain_allowance) == (37, 50)


def test_owned_is_read_as_a_count(tmp_path: Path) -> None:
    """The row is a decision, not an inventory: 27 ids per line for 5,192 lines."""
    path = write(tmp_path / "j.jsonl", json.dumps({"owned": ["a", "b", "c"]}))

    (row,) = read_rows(path)[0]

    assert row.owned_count == 3


def test_a_field_of_the_wrong_type_is_dropped_rather_than_fatal(tmp_path: Path) -> None:
    """`RoomJournal.write` serialises with `default=str`, so a field's type is not
    guaranteed by the file — and the evening's record must not be unreadable for it."""
    path = write(tmp_path / "j.jsonl", json.dumps({"price": "not a number", "owned": 3}))

    (row,) = read_rows(path)[0]

    assert row.price is None
    assert row.owned_count is None


def test_what_the_writer_writes_round_trips(tmp_path: Path) -> None:
    """The two halves of this module, against each other rather than against a fixture."""
    path = tmp_path / "j.jsonl"
    with RoomJournal(path) as journal:
        journal.write({"at_ms": 7, "decision": "bid", "lot": "abc", "price": 12})

    (row,) = read_rows(path)[0]

    assert (row.at_ms, row.decision, row.lot, row.price) == (7, "bid", "abc", 12.0)


class TestTheWriterAndTheReaderAgree:
    """The drift guard. Reading the writer's own dict literals, not trusting a memory.

    Both the row builders and `RoomTracker.cycle`'s journal call live in
    `application/asta_room.py`, which cannot import this module (it takes a callable, so
    the journal stays injectable and the outage rule holds). Nothing structural therefore
    connects the two ends — which is exactly how three keys came to be written and never
    read.
    """

    @staticmethod
    def _keys_written() -> set[str]:
        source = module_file("fantabot.application.asta_room").read_text(encoding="utf-8")
        tree = ast.parse(source)
        journal_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_journal"
        ]
        rows = [
            node
            for name in ("waiting_row", "error_row")
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        assert journal_calls, "no `self._journal(...)` call found — this scan reads nothing"
        assert len(rows) == 2, "waiting_row/error_row moved; this scan reads nothing"

        keys: set[str] = set()
        for node in ast.walk(ast.Module(body=[*journal_calls, *rows], type_ignores=[])):
            if isinstance(node, ast.Dict):
                keys.update(
                    k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
        return keys

    def test_every_key_the_writer_emits_has_a_field_here(self) -> None:
        unread = sorted(self._keys_written() - ROW_FIELDS)
        assert not unread, (
            f"the writer emits {unread} and the reader has no field for it — this is the "
            "`bargain_spent`/`bargain_allowance`/`error` drift again"
        )

    def test_the_scan_finds_the_keys_it_claims_to(self) -> None:
        """A scan over nothing reports success. `_paths.pkgs` exists for this reason."""
        assert {"at_ms", "decision", "bargain_spent", "error"} <= self._keys_written()


def test_every_field_is_optional_because_the_file_spans_two_commands() -> None:
    """`cycle_ms` was added after the 2026-09-01 evening was recorded; `asta room` and
    `asta bid` both append here and neither marks a run boundary."""
    empty = JournalEntry(index=1)

    assert empty.index == 1
    assert all(
        getattr(empty, f.name) is None for f in dataclasses.fields(empty) if f.name != "index"
    )


def test_row_fields_names_journal_keys_not_python_attributes() -> None:
    """`owned` is a list of 27 ids in the file and an `owned_count` on the row. The drift
    guard compares against what the *writer* emits, so this set is the file's vocabulary."""
    assert "owned" in ROW_FIELDS
    assert "owned_count" not in ROW_FIELDS
    assert "index" not in ROW_FIELDS

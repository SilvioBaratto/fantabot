"""Carrying the landing zone into Postgres, incrementally and safely.

The database is deliberately not on the collection critical path: the collector
appends to a file and this reads from it. So the loader's job is to be
interruptible and resumable without ever consuming a record the writer has not
finished writing.

Everything here is the reading half, which is where the correctness lives. It
needs no database.
"""

from __future__ import annotations

import json
from pathlib import Path

from fantabot.aste.loader import Checkpoint, read_from


def _write(path: Path, *records: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _record(price: int) -> dict:
    return {"seen_at": "2026-08-27T07:00:00+00:00", "auction_id": "a-1",
            "state": {"price": price, "last_update": price}}


def test_reading_from_zero_returns_everything(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _write(path, _record(1), _record(2))
    records, offset = read_from(path, 0)
    assert [r["state"]["price"] for r in records] == [1, 2]
    assert offset == path.stat().st_size


def test_reading_from_the_last_offset_returns_only_what_is_new(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _write(path, _record(1))
    _, offset = read_from(path, 0)
    _write(path, _record(2), _record(3))
    records, new_offset = read_from(path, offset)
    assert [r["state"]["price"] for r in records] == [2, 3]
    assert new_offset == path.stat().st_size


def test_a_half_written_line_is_left_for_the_next_pass(tmp_path: Path) -> None:
    """The writer appends while this reads. Consuming a partial line would drop
    the record permanently — the offset would move past it and it would never be
    read again, which is the one failure a landing zone exists to prevent."""
    path = tmp_path / "events.jsonl"
    _write(path, _record(1))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seen_at": "2026", "auction_id": "a-1", "sta')

    records, offset = read_from(path, 0)
    assert [r["state"]["price"] for r in records] == [1]
    assert offset < path.stat().st_size, "the offset must stop before the partial line"

    # The writer finishes the line; the next pass picks it up whole.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('te": {"price": 9, "last_update": 9}}\n')
    more, _ = read_from(path, offset)
    assert [r["state"]["price"] for r in more] == [9]


def test_nothing_new_reads_as_nothing(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _write(path, _record(1))
    _, offset = read_from(path, 0)
    assert read_from(path, offset) == ([], offset)


def test_a_missing_file_is_not_an_error_before_collection_starts(tmp_path: Path) -> None:
    assert read_from(tmp_path / "absent.jsonl", 0) == ([], 0)


def test_a_checkpoint_survives_a_restart(tmp_path: Path) -> None:
    """A loader that forgets its offset re-reads 80 MB on every start."""
    path = tmp_path / "events.jsonl"
    checkpoint = Checkpoint(path)
    assert checkpoint.read() == 0
    checkpoint.write(1234)
    assert Checkpoint(path).read() == 1234


def test_a_checkpoint_ahead_of_a_shrunken_file_resets(tmp_path: Path) -> None:
    """A rotated or replaced landing zone is shorter than the offset remembers.
    Seeking past the end would silently read nothing, for ever."""
    path = tmp_path / "events.jsonl"
    _write(path, _record(1))
    records, _ = read_from(path, path.stat().st_size + 10_000)
    assert [r["state"]["price"] for r in records] == [1], "a stale offset must not blind us"


def test_the_command_is_registered_with_its_flags() -> None:
    import re

    from typer.testing import CliRunner

    from fantabot.cli import app

    result = CliRunner().invoke(app, ["aste-load", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    for flag in ("--seed", "--follow", "--interval", "--dry-run"):
        assert flag in plain, f"{flag} is missing from the help"


def test_a_dry_run_reports_progress_without_a_database(tmp_path: Path) -> None:
    import re

    from typer.testing import CliRunner

    from fantabot.cli import app

    landing = tmp_path / "events.jsonl"
    _write(landing, _record(1), _record(2))
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([["a-1", "4", 8, 500, 25, 25, "random", "free", 8, 8, "x"]]))

    result = CliRunner().invoke(
        app, ["aste-load", str(landing), "--seed", str(seed), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "carried 2" in plain
    assert "0 bytes behind" in plain

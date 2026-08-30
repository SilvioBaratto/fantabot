"""The landing-zone writer: the one component that must not fail.

Everything else in this phase can be rerun. A frame that never reached disk is
gone, so the writer's contract is narrow and its failure modes are the tests.

Append-and-close per record rather than a held handle. It costs a syscall and
buys the property that matters: the collector was killed eleven times in eight
hours on 2026-08-26, and a buffered handle would have lost whatever the buffer
held each time.
"""

from __future__ import annotations

import json
from pathlib import Path

from _paths import ONE_AUCTION

from fantabot.aste.landing import LandingZone, read_records


def test_a_record_is_readable_immediately_after_writing(tmp_path: Path) -> None:
    """Not "after closing". A collector that is killed mid-evening must leave
    every already-written record intact and parseable."""
    zone = LandingZone(tmp_path / "events.jsonl")
    zone.write("a-1", {"price": 1})
    assert len(read_records(tmp_path / "events.jsonl")) == 1


def test_records_carry_the_auction_and_an_observation_time(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    LandingZone(path).write("a-1", {"price": 1})
    (record,) = read_records(path)
    assert record["auction_id"] == "a-1"
    assert record["state"] == {"price": 1}
    assert record["seen_at"], "an observation without a time cannot be ordered"


def test_the_shape_matches_what_the_backfill_already_reads(tmp_path: Path) -> None:
    """The live path and the recorded evening must be the same file format, or
    the loader needs two readers and one of them stays untested."""
    recorded = json.loads(
        ONE_AUCTION
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    path = tmp_path / "events.jsonl"
    LandingZone(path).write(recorded["auction_id"], recorded["state"])
    (written,) = read_records(path)
    assert set(written) >= {"seen_at", "auction_id", "state"}
    assert set(recorded) >= set(written) - {"ns"}


def test_appending_never_rewrites_what_is_already_there(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    zone = LandingZone(path)
    for i in range(5):
        zone.write("a-1", {"price": i})
    assert [r["state"]["price"] for r in read_records(path)] == [0, 1, 2, 3, 4]


def test_a_truncated_final_line_does_not_poison_the_whole_file(tmp_path: Path) -> None:
    """A kill during a write can leave a partial line. Losing that one record is
    the accepted cost; losing the file is not."""
    path = tmp_path / "events.jsonl"
    zone = LandingZone(path)
    zone.write("a-1", {"price": 1})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"auction_id": "a-1", "sta')
    records = read_records(path)
    assert len(records) == 1
    assert records[0]["state"]["price"] == 1


def test_the_directory_is_created_if_it_does_not_exist(tmp_path: Path) -> None:
    zone = LandingZone(tmp_path / "nested" / "deeper" / "events.jsonl")
    zone.write("a-1", {"price": 1})
    assert (tmp_path / "nested" / "deeper" / "events.jsonl").exists()


def test_unicode_survives_the_round_trip(tmp_path: Path) -> None:
    """Player names carry accents — Konaté, Lucumì. A file written with escaped
    ASCII still parses, but it stops matching the recorded evening byte for byte."""
    path = tmp_path / "events.jsonl"
    LandingZone(path).write("a-1", {"nome": "Konaté A."})
    assert "Konaté" in path.read_text(encoding="utf-8")


def test_a_record_is_on_disk_before_the_writer_exits(tmp_path: Path) -> None:
    """Read from a *separate process*, which is the only reader that cannot be
    fooled by a buffer.

    The docstring claims a `kill -9` loses at most the line in flight. Mutation
    testing on 2026-08-27 replaced append-and-close with a held handle and every
    test still passed — because a same-process read sees data that CPython has
    flushed on garbage collection, which a killed process never does. This is the
    assertion that mutant fails.
    """
    import subprocess
    import sys
    import textwrap

    path = tmp_path / "events.jsonl"
    LandingZone(path).write("a-1", {"price": 7})

    # A fresh interpreter: no shared buffers, no shared file objects.
    script = textwrap.dedent(
        f"""
        import json, sys
        lines = open({str(path)!r}, encoding="utf-8").read().splitlines()
        assert len(lines) == 1, f"expected one durable record, found {{len(lines)}}"
        assert json.loads(lines[0])["state"]["price"] == 7
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_write_is_durable_not_only_the_last(tmp_path: Path) -> None:
    """A handle flushed once at the end would pass the single-record case."""
    import subprocess
    import sys

    path = tmp_path / "events.jsonl"
    zone = LandingZone(path)
    for i in range(50):
        zone.write("a-1", {"price": i})

    script = (
        f"lines = open({str(path)!r}, encoding='utf-8').read().splitlines()\n"
        "assert len(lines) == 50, len(lines)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

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
import tracemalloc
from pathlib import Path

import pytest

from fantabot.aste.loader import (
    CachedPlayerIds,
    Checkpoint,
    LandingZoneMissing,
    SeedRows,
    iter_records,
    read_from,
    read_records,
)


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


def test_a_missing_file_is_an_error_because_it_means_nothing_is_collecting(
    tmp_path: Path,
) -> None:
    """This test previously asserted the opposite, and the belief was the bug.

    "A missing landing zone is not an error, collection may not have started
    yet" sounds reasonable and produced a loader that printed `carried 0` for
    ever while nothing was running. Present-but-empty is the state that means
    "just started"; absent means "nothing is collecting into it", and only one
    of those has a remedy the operator needs telling.
    """
    from fantabot.aste.loader import LandingZoneMissing

    with pytest.raises(LandingZoneMissing, match="harvest collect"):
        read_from(tmp_path / "absent.jsonl", 0)


def test_an_empty_file_is_genuinely_quiet(tmp_path: Path) -> None:
    empty = tmp_path / "live.jsonl"
    empty.touch()
    assert read_from(empty, 0) == ([], 0)


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

    result = CliRunner().invoke(app, ["harvest", "load", "--help"])
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
        app, ["harvest", "load", str(landing), "--seed", str(seed), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "carried 2" in plain
    assert "0 bytes behind" in plain


def test_a_missing_landing_zone_is_named_not_reported_as_quiet(tmp_path: Path) -> None:
    """`carried 0 · 0 bytes behind` printed identically for a file that does not
    exist, one that is empty, and one already fully loaded.

    Observed 2026-08-27: the operator ran scan and load, skipped `harvest collect`,
    and watched a healthy-looking loader report zero indefinitely. It is the
    failure shape this phase guards against everywhere else — a 401 and a quiet
    night must not read the same — left standing in our own loader.
    """
    import re

    from typer.testing import CliRunner

    from fantabot.cli import app

    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([["a-1", "4", 8, 500, 25, 25, "random", "free", 8, 8, "x"]]))
    absent = tmp_path / "never-created.jsonl"

    result = CliRunner().invoke(
        app, ["harvest", "load", str(absent), "--seed", str(seed), "--dry-run"]
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "never-created.jsonl" in plain, "the missing file must be named"
    assert "harvest collect" in plain, "and so must the command that would create it"
    assert "carried 0" not in plain, "a missing file is not a quiet pass"


def test_an_empty_landing_zone_reads_differently_from_a_missing_one(tmp_path: Path) -> None:
    """Present-but-empty is the collector having just started. That is genuinely
    quiet, and must not be reported as an error."""
    import re

    from typer.testing import CliRunner

    from fantabot.cli import app

    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([["a-1", "4", 8, 500, 25, 25, "random", "free", 8, 8, "x"]]))
    empty = tmp_path / "live.jsonl"
    empty.touch()

    result = CliRunner().invoke(
        app, ["harvest", "load", str(empty), "--seed", str(seed), "--dry-run"]
    )
    assert result.exit_code == 0
    assert "carried 0" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)


class TestCachedPlayerIds:
    """`known_player_ids()` was called inside the pass body.

    Following a landing zone at the default ten-second interval, that is a
    session opened and 1,492 ids pulled across the wire six times a minute, for
    a table that changes when the quotazioni scraper runs — which is to say
    almost never, but not never, so the answer cannot simply be cached for the
    life of the process.
    """

    def test_the_first_pass_fetches(self) -> None:
        calls = []
        cache = CachedPlayerIds(lambda: (calls.append(1), frozenset({7}))[1], ttl=300.0)
        assert cache.get(now=0.0) == frozenset({7})
        assert len(calls) == 1

    def test_a_later_pass_within_the_ttl_reuses_the_answer(self) -> None:
        calls: list[int] = []
        cache = CachedPlayerIds(lambda: (calls.append(1), frozenset({7}))[1], ttl=300.0)
        cache.get(now=0.0)
        cache.get(now=10.0)
        cache.get(now=299.9)
        assert len(calls) == 1, "the table did not change; neither should the query count"

    def test_the_answer_is_refetched_once_the_ttl_has_passed(self) -> None:
        answers = [frozenset({7}), frozenset({7, 8})]
        cache = CachedPlayerIds(lambda: answers.pop(0), ttl=300.0)
        assert cache.get(now=0.0) == frozenset({7})
        assert cache.get(now=300.0) == frozenset({7, 8}), (
            "a scrape during a long follow must eventually be seen"
        )

    def test_a_failed_fetch_does_not_poison_the_cache(self) -> None:
        """A momentary database outage must not turn every player unlinked.

        `harvest load --follow` already survives an outage by skipping the pass;
        a cache that stored the failure would keep nulling `fantacalcio_id`
        long after the database came back.
        """

        def explode() -> frozenset[int]:
            raise RuntimeError("database unreachable")

        cache = CachedPlayerIds(explode, ttl=300.0)
        with pytest.raises(RuntimeError):
            cache.get(now=0.0)
        cache._fetch = lambda: frozenset({7})  # type: ignore[method-assign]
        assert cache.get(now=0.1) == frozenset({7})


class TestTheSeedIsRereadWhileFollowing:
    """`harvest load --follow` read the seed once, and dropped what it missed.

    Measured 2026-08-27 22:07. The collector re-reads its seed and adopts
    auctions that opened since; the loader did not, so every adopted auction's
    events were foreign to it. It printed `595 record(s) dropped — unknown
    auction 595` and advanced its checkpoint past them: not a delay, a loss.

    The drop counter added an hour earlier is the only reason this was a line
    in the log rather than a hole in the table.
    """

    def test_a_seed_that_grew_is_picked_up_on_the_next_pass(self, tmp_path: Path) -> None:
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps([["a-0", "17"]]), encoding="utf-8")
        source = SeedRows(seed)
        assert [row[0] for row in source.read()] == ["a-0"]

        seed.write_text(json.dumps([["a-0", "17"], ["a-1", "18"]]), encoding="utf-8")
        assert [row[0] for row in source.read()] == ["a-0", "a-1"]

    def test_a_half_written_seed_keeps_the_last_good_one(self, tmp_path: Path) -> None:
        """`harvest scan` rewrites the file this reads. Catching it mid-write must
        cost one pass, not turn every auction into an unknown one — which is
        the very loss this re-read exists to stop."""
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps([["a-0", "17"]]), encoding="utf-8")
        source = SeedRows(seed)
        source.read()

        seed.write_text('[["a-0", "17"], ["a-1"', encoding="utf-8")
        assert [row[0] for row in source.read()] == ["a-0"]
        assert source.failures == 1

        seed.write_text(json.dumps([["a-0", "17"], ["a-1", "18"]]), encoding="utf-8")
        assert len(source.read()) == 2
        assert source.failures == 1, "a recovered read must not keep counting"

    def test_a_file_that_parses_but_is_not_a_seed_is_refused(self, tmp_path: Path) -> None:
        """Valid JSON is not the bar; a list of rows is."""
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps([["a-0", "17"]]), encoding="utf-8")
        source = SeedRows(seed)
        source.read()
        seed.write_text('{"auctions": []}', encoding="utf-8")
        assert [row[0] for row in source.read()] == ["a-0"]
        assert source.failures == 1

    def test_a_seed_deleted_under_us_is_not_fatal(self, tmp_path: Path) -> None:
        seed = tmp_path / "seed.json"
        seed.write_text(json.dumps([["a-0", "17"]]), encoding="utf-8")
        source = SeedRows(seed)
        source.read()
        seed.unlink()
        assert [row[0] for row in source.read()] == ["a-0"]


class TestTheWholeFilePassIsStreamed:
    """Rebuilding assignments from the whole landing zone must not hold it.

    The windowing fix made every pass reconstruct from the entire file, which is
    correct — a window starting mid-turn rebuilds a ladder from nothing — but
    `read_from` reads it with a single `handle.read()`, so the bytes, the
    decoded string, the list of split lines and the list of parsed dicts are all
    alive at once.

    Measured 2026-08-27 22:27: a 92 MB landing zone, 175,803 records, and the
    loader resident at **1.6 GB** — a tenth of the machine, re-paid every ten
    seconds. The file was growing 3 MB a minute with hours of auctions left,
    so the run this was told to leave up all night would not have survived it.
    """

    def _zone(self, path: Path, records: int) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for i in range(records):
                handle.write(
                    json.dumps(
                        {
                            "auction_id": f"a-{i % 50}",
                            "seen_at": "2026-08-27T22:00:00+00:00",
                            "state": {"last_update": i, "price": i % 90, "pad": "x" * 400},
                        }
                    )
                    + "\n"
                )

    def test_it_yields_lazily_rather_than_returning_a_list(self, tmp_path: Path) -> None:
        path = tmp_path / "live.jsonl"
        self._zone(path, 5)
        stream = iter_records(path)
        assert not isinstance(stream, list), "a list is the thing that cost 1.6 GB"
        assert next(iter(stream))["auction_id"] == "a-0"

    def test_it_reads_the_same_records_as_the_eager_reader(self, tmp_path: Path) -> None:
        path = tmp_path / "live.jsonl"
        self._zone(path, 200)
        assert list(iter_records(path)) == read_records(path)

    def test_a_line_still_being_written_is_left_for_the_next_pass(self, tmp_path: Path) -> None:
        path = tmp_path / "live.jsonl"
        self._zone(path, 3)
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"auction_id": "a-9", "state": {"pri')
        assert len(list(iter_records(path))) == 3

    def test_a_missing_zone_is_still_named_rather_than_read_as_empty(self, tmp_path: Path) -> None:
        with pytest.raises(LandingZoneMissing):
            list(iter_records(tmp_path / "never-created.jsonl"))

    def test_it_holds_a_fraction_of_what_the_eager_reader_holds(self, tmp_path: Path) -> None:
        """The property that regressed, measured rather than asserted."""
        path = tmp_path / "live.jsonl"
        self._zone(path, 20_000)

        tracemalloc.start()
        eager = read_records(path)
        eager_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert len(eager) == 20_000
        del eager

        tracemalloc.start()
        seen = sum(1 for _ in iter_records(path))
        streamed_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert seen == 20_000

        assert streamed_peak < eager_peak / 10, (
            f"streaming held {streamed_peak:,} bytes against {eager_peak:,} eager — "
            "the point is that the file's size stops being the loader's size"
        )

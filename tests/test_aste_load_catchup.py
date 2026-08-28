"""The backlog the landing zone was built to survive, and could not carry.

The split exists for one promise, written at the top of `loader.py`: *an outage
costs catch-up time and never a record*. `read_from` read from the checkpoint to
EOF in a single `handle.read()`, holding the bytes, the slice copy, the decoded
string, the split lines and every parsed dict at once — roughly seventeen times
the window, measured at 1.6 GB for 92 MB on 2026-08-27.

That is survivable at the only size the suite ever exercised. `test_aste_outage`
catches up on **three** records; a live `--follow` runs about 150 kB behind. It
is not survivable at the size a real gap reaches: the loader was left stopped at
2026-08-27 22:27 while the collector kept running, and by 08:17 the next morning
the checkpoint sat 108 MB into a 1,249 MB file — a **1.14 GB backlog, 2,242,083
records**, on a 16 GB machine also running the collector.

So the promise held for the outage the tests imagined and failed for the outage
that happened, and it failed in the direction that loses the evening: the one
command that would have carried the data is the one that cannot be started.

A pass carries a bounded window. Catch-up is many passes, and the follow loop
does not wait an interval between them while a full window is still owed.
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

from fantabot.aste.loader import (
    DEFAULT_WINDOW_BYTES,
    catching_up,
    read_from,
)


def _zone(path: Path, records: int, pad: int = 400) -> None:
    """A landing zone shaped like the real one: ~500 bytes a record."""
    with path.open("w", encoding="utf-8") as handle:
        for i in range(records):
            handle.write(
                json.dumps(
                    {
                        "auction_id": f"a-{i % 50}",
                        "seen_at": "2026-08-27T22:00:00+00:00",
                        "state": {"last_update": i, "price": i % 90, "pad": "x" * pad},
                    }
                )
                + "\n"
            )


def _drain(path: Path, max_bytes: int) -> list[dict]:
    """Every record a capped loader eventually carries, in order."""
    carried: list[dict] = []
    offset = 0
    while True:
        records, offset = read_from(path, offset, max_bytes=max_bytes)
        if not records:
            return carried
        carried.extend(records)


class TestOnePassCarriesABoundedWindow:
    def test_a_backlog_larger_than_the_window_is_not_taken_in_one_pass(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "live.jsonl"
        _zone(path, 400)
        size = path.stat().st_size
        assert size > 20_000, "the fixture must be bigger than the window under test"

        records, offset = read_from(path, 0, max_bytes=10_000)

        assert offset < size, "the pass consumed the whole backlog it was capped against"
        assert 0 < len(records) < 400
        assert offset <= 10_000 + 1, "more than a window's worth of bytes was consumed"

    def test_the_offset_always_lands_on_a_record_boundary(self, tmp_path: Path) -> None:
        """Advancing past half a line would lose it: the checkpoint never returns."""
        path = tmp_path / "live.jsonl"
        _zone(path, 400)
        raw = path.read_bytes()

        offset = 0
        for _ in range(20):
            records, offset = read_from(path, offset, max_bytes=10_000)
            if not records:
                break
            assert offset == 0 or raw[offset - 1 : offset] == b"\n"

    def test_a_line_the_collector_is_still_writing_is_left_alone(self, tmp_path: Path) -> None:
        path = tmp_path / "live.jsonl"
        _zone(path, 3)
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"auction_id": "a-9", "state": {"pri')
        whole = path.stat().st_size

        records, offset = read_from(path, 0, max_bytes=DEFAULT_WINDOW_BYTES)

        assert len(records) == 3
        assert offset < whole, "the half-written line was consumed"

    def test_a_record_longer_than_the_window_still_moves(self, tmp_path: Path) -> None:
        """Otherwise the loader stops for ever on one fat line, reading zero
        records and reporting a quiet pass — the failure this package keeps
        having to name."""
        path = tmp_path / "live.jsonl"
        _zone(path, 2, pad=50_000)

        records, offset = read_from(path, 0, max_bytes=1_000)

        assert len(records) == 1, "no progress past a record wider than the window"
        assert offset > 1_000


class TestCatchUpLosesNothing:
    def test_repeated_passes_carry_exactly_what_one_uncapped_pass_would(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "live.jsonl"
        _zone(path, 1_000)

        whole, _ = read_from(path, 0, max_bytes=DEFAULT_WINDOW_BYTES)
        chunked = _drain(path, max_bytes=9_000)

        assert chunked == whole
        assert len(chunked) == 1_000

    def test_records_appended_mid_catch_up_are_picked_up_too(self, tmp_path: Path) -> None:
        """The collector does not stop while the loader is catching up."""
        path = tmp_path / "live.jsonl"
        _zone(path, 200)

        records, offset = read_from(path, 0, max_bytes=9_000)
        assert len(records) < 200

        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"auction_id": "late", "seen_at": "2026", "state": {}}) + "\n")

        rest: list[dict] = []
        while True:
            more, offset = read_from(path, offset, max_bytes=9_000)
            if not more:
                break
            rest.extend(more)
        assert rest[-1]["auction_id"] == "late"
        assert len(records) + len(rest) == 201


class TestItHoldsTheWindowRatherThanTheBacklog:
    """The property that made the real backlog unloadable, measured."""

    def test_a_capped_pass_costs_a_fraction_of_an_uncapped_one(self, tmp_path: Path) -> None:
        path = tmp_path / "live.jsonl"
        _zone(path, 20_000)

        tracemalloc.start()
        uncapped, _ = read_from(path, 0, max_bytes=DEFAULT_WINDOW_BYTES)
        uncapped_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert len(uncapped) == 20_000

        del uncapped
        tracemalloc.start()
        capped, _ = read_from(path, 0, max_bytes=200_000)
        capped_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert capped, "the capped pass must still carry something"

        assert capped_peak * 4 < uncapped_peak, (
            f"capped {capped_peak} vs uncapped {uncapped_peak}: the window is not bounding "
            "what a pass holds, which is what put 1.14 GB out of reach"
        )


class TestTheFollowLoopDoesNotWaitOutABacklog:
    """`--follow` sleeps `interval` between passes, which is right while it is
    keeping up and wrong while it is behind: 1.14 GB at a 32 MB window is 36
    passes, and sleeping ten seconds between them adds six minutes to a catch-up
    that is otherwise disk-bound."""

    def test_a_live_lag_is_not_catching_up(self) -> None:
        assert catching_up(0) is False
        assert catching_up(150_000) is False, "the normal following lag must still sleep"

    def test_a_backlog_of_a_full_window_or_more_is(self) -> None:
        assert catching_up(DEFAULT_WINDOW_BYTES) is True
        assert catching_up(1_141_167_709) is True

    def test_the_window_it_compares_against_can_be_given(self) -> None:
        assert catching_up(5_000, window=1_000) is True
        assert catching_up(500, window=1_000) is False


class TestTheFollowLoopCatchesUpWithoutWaitingBetweenPasses:
    """The loop, driven end to end with the database faked.

    `catching_up` on its own proves the rule; this proves `aste-load` asks it.
    The bug it guards is one an operator would read as working: passes go by,
    each one carries records, and the run is simply thirty-six intervals slower
    than the disk it is reading.
    """

    class _Stop(Exception):
        """Raised from the faked sleep, to end a loop that has no other end."""

    def _seed(self, path: Path) -> Path:
        seed = path / "seed.json"
        seed.write_text(
            json.dumps([["a-0", "4", 8, 500, 25, 25, "random", "free", 8, 8, "L", "mantra"]]),
            encoding="utf-8",
        )
        return seed

    def _fake_database(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import contextlib

        from fantabot.db import database_manager
        from fantabot.db.repositories import aste as aste_repo

        class _Session:
            def commit(self) -> None:
                return None

        @contextlib.contextmanager
        def _session():  # type: ignore[no-untyped-def]
            yield _Session()

        class _Repo:
            def __init__(self, _session: object) -> None:
                pass

            def upsert_auctions(self, rows: object) -> int:
                return 0

            def upsert_events(self, rows: object) -> int:
                return 0

            def upsert_assignments(self, rows: object) -> int:
                return 0

            def known_player_ids(self) -> frozenset[int]:
                return frozenset()

        monkeypatch.setattr(database_manager, "get_session", _session)
        monkeypatch.setattr(aste_repo, "AsteRepository", _Repo)

    def test_it_carries_pass_after_pass_and_only_sleeps_once_caught_up(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        import re
        import time

        from typer.testing import CliRunner

        from fantabot.cli import app

        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)
        assert landing.stat().st_size > 8 * 20_000, "the backlog must span several windows"
        self._fake_database(monkeypatch)

        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            raise self._Stop

        monkeypatch.setattr(time, "sleep", _sleep)

        result = CliRunner().invoke(
            app,
            [
                "aste-load",
                str(landing),
                "--seed",
                str(self._seed(tmp_path)),
                "--listone",
                str(tmp_path / "no-listone.json"),
                "--follow",
                "--window",
                "20000",
            ],
        )

        assert isinstance(result.exception, self._Stop), result.output
        carried = len(re.findall(r"carried \d+", re.sub(r"\x1b\[[0-9;]*m", "", result.output)))
        assert sleeps == [10.0], "it slept before the backlog was carried"
        assert carried >= 8, (
            f"only {carried} pass(es) before the first sleep: the loop is waiting out a "
            "backlog it could have read straight through"
        )

    def test_a_dry_run_still_sleeps_because_its_checkpoint_never_moves(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Without this it would re-read the same window for ever, at full speed."""
        import time

        from typer.testing import CliRunner

        from fantabot.cli import app

        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)

        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            raise self._Stop

        monkeypatch.setattr(time, "sleep", _sleep)

        result = CliRunner().invoke(
            app,
            [
                "aste-load",
                str(landing),
                "--seed",
                str(self._seed(tmp_path)),
                "--listone",
                str(tmp_path / "no-listone.json"),
                "--follow",
                "--dry-run",
                "--window",
                "20000",
            ],
        )

        assert isinstance(result.exception, self._Stop), result.output
        assert sleeps == [10.0]

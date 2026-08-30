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


def _zone(path: Path, records: int, pad: int = 340) -> None:
    """A landing zone shaped like the real one: ~500 bytes a record, and it sells.

    **It used not to sell, and that made several assertions below vacuous.** The states
    carried only `last_update`, `price` and padding — no `update_type`, no `player_id` —
    so `reconstruct` returned zero assignments for any zone built here, and every
    assertion about assignment batches was satisfied by a list of zeros. Found on
    2026-08-30 while replacing the whole-file rebuild, by running `reconstruct` over
    this fixture and getting nothing.

    Each auction now runs a repeating turn: `first_call`, two raises, a close. `pad` is
    trimmed to keep the record near 500 bytes, because the window-size arithmetic in
    this module is calibrated on it.
    """
    turn = ("first_call", "raise", "raise", "close_auction")
    with path.open("w", encoding="utf-8") as handle:
        for i in range(records):
            step = i % len(turn)
            handle.write(
                json.dumps(
                    {
                        "auction_id": f"a-{i % 50}",
                        "seen_at": "2026-08-27T22:00:00+00:00",
                        "state": {
                            "last_update": i,
                            "update_type": turn[step],
                            "player_id": f"p-{i // len(turn) % 7}",
                            "price": step * 3,
                            "fantateam_id": None if step == 0 else f"t-{step}",
                            "pad": "x" * pad,
                        },
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

    `catching_up` on its own proves the rule; this proves `harvest load` asks it.
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

        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories import aste as aste_repo

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
                "harvest", "load",
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
                "harvest", "load",
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


class _FakeDatabase:
    """The repository, recording what each pass asked it to write."""

    def __init__(self) -> None:
        self.assignment_batches: list[int] = []
        self.event_batches: list[int] = []
        #: Every assignment row written, in order — the union a multi-pass run
        #: produces, which is what the equivalence oracle compares.
        self.assignment_rows: list[dict] = []

    def install(self, monkeypatch, keep_rows: bool = False) -> None:  # type: ignore[no-untyped-def]
        import contextlib

        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories import aste as aste_repo

        record = self

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

            def upsert_events(self, rows: list) -> int:  # type: ignore[type-arg]
                record.event_batches.append(len(rows))
                return len(rows)

            def upsert_assignments(self, rows: list) -> int:  # type: ignore[type-arg]
                record.assignment_batches.append(len(rows))
                if keep_rows:
                    record.assignment_rows.extend(rows)
                return len(rows)

            def known_player_ids(self) -> frozenset[int]:
                return frozenset()

        monkeypatch.setattr(database_manager, "get_session", _session)
        monkeypatch.setattr(aste_repo, "AsteRepository", _Repo)


def _seed_file(path: Path) -> Path:
    """A seed naming every auction `_zone` writes, so nothing is dropped."""
    seed = path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                [f"a-{i}", "4", 8, 500, 25, 25, "random", "free", 8, 8, "L", "mantra"]
                for i in range(50)
            ]
        ),
        encoding="utf-8",
    )
    return seed


def _load(landing: Path, seed: Path, tmp_path: Path, *extra: str) -> object:
    from typer.testing import CliRunner

    from fantabot.cli import app

    return CliRunner().invoke(
        app,
        [
            "harvest", "load",
            str(landing),
            "--seed",
            str(seed),
            "--listone",
            str(tmp_path / "no-listone.json"),
            *extra,
        ],
    )


def _carried(output: str) -> list[int]:
    import re

    return [int(n) for n in re.findall(r"carried (\d+)", re.sub(r"\x1b\[[0-9;]*m", "", output))]


class TestAOneShotLoadCarriesTheWholeBacklog:
    """`harvest load` without `--follow` promises, in its own docstring, to *carry
    the landing zone*. Capping the window quietly turned that into "carry 32 MB
    and exit 0" — a partial load reported as a complete one, which is the shape
    this repo keeps having to name. `--follow` should mean only *keep watching
    after catching up*, never *the only mode that catches up*.
    """

    def test_it_keeps_going_until_the_backlog_is_carried(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)
        assert landing.stat().st_size > 8 * 20_000, "the backlog must span several windows"
        _FakeDatabase().install(monkeypatch)

        result = _load(landing, _seed_file(tmp_path), tmp_path, "--window", "20000")

        assert result.exit_code == 0, result.output
        carried = _carried(result.output)
        assert len(carried) >= 8, f"stopped after {len(carried)} window(s) with a backlog left"
        assert sum(carried) == 400, f"{sum(carried)} of 400 records were carried"

    def test_a_zone_already_carried_makes_exactly_one_pass(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        landing = tmp_path / "live.jsonl"
        _zone(landing, 5)
        _FakeDatabase().install(monkeypatch)

        result = _load(landing, _seed_file(tmp_path), tmp_path)

        assert result.exit_code == 0, result.output
        assert _carried(result.output) == [5]

    def test_a_pass_that_reads_nothing_does_not_spin(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Looping on lack of progress is worse than stopping short of it."""
        landing = tmp_path / "live.jsonl"
        landing.write_text("", encoding="utf-8")
        _FakeDatabase().install(monkeypatch)

        result = _load(landing, _seed_file(tmp_path), tmp_path, "--window", "20000")

        assert result.exit_code == 0, result.output
        assert _carried(result.output) == [0]


class TestTheLadderIsCarriedRatherThanRebuilt:
    """**This class replaced one whose subject no longer exists.**

    It used to assert that the whole-file rebuild ran *once* per catch-up rather than
    once per window — 9.4 s and ~880 MB for 2,242,083 records, thirty-four times back
    to back on the 2026-08-28 backlog, each result thrown away by the next pass. The
    fix then was to defer the rebuild while another pass was certain.

    The incremental fold removes the rebuild, so the deferral has nothing left to
    defer — and keeping it would now be *harmful*: the byte offset advances on every
    pass, so a pass that skipped the fold would never see those records again. Every
    pass folds and writes its own sales.

    So the property inverts. It was "the expensive thing happens once"; it is now
    "every pass carries its own share, and the union is exactly what one whole-file
    pass would have produced". That is strictly stronger, and it is checked against
    `reconstruct` rather than against a call count.
    """

    def test_every_pass_writes_its_own_assignments(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)
        database = _FakeDatabase()
        database.install(monkeypatch)

        result = _load(landing, _seed_file(tmp_path), tmp_path, "--window", "20000")

        assert result.exit_code == 0, result.output
        assert len(_carried(result.output)) >= 8, "the fixture did not span several windows"
        assert len(database.event_batches) >= 8, "events were never deferred"
        assert sum(1 for n in database.assignment_batches if n) > 1, (
            "only one pass wrote sales — the fold is being skipped, and the offset "
            "advances anyway, so those records are gone"
        )

    def test_the_whole_file_is_never_re_read(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """The thing this whole exercise was for.

        `assignments_for_pass` is the whole-file rebuild. It still exists for
        `harvest backfill`, which genuinely reads a finished recording, but the
        follower must never call it.
        """
        from fantabot.aste import loader

        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)
        _FakeDatabase().install(monkeypatch)

        calls: list[int] = []
        real = loader.assignments_for_pass
        monkeypatch.setattr(
            loader,
            "assignments_for_pass",
            lambda *a, **k: (calls.append(1), real(*a, **k))[1],
        )

        _load(landing, _seed_file(tmp_path), tmp_path, "--window", "20000")

        assert calls == [], f"the whole-file rebuild ran {len(calls)} times"

    def test_the_union_across_passes_equals_one_whole_file_fold(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """Ladders included — a short ladder overwrites a complete one, silently.

        `compare.equivalent` is the oracle rather than a row count, because a count
        cannot see a truncated ladder and that is the failure the whole-file rebuild
        existed to prevent.
        """
        from fantabot.aste.compare import equivalent
        from fantabot.aste.loader import iter_records
        from fantabot.aste.models import Assignment, Bid
        from fantabot.aste.reconstruct import reconstruct

        landing = tmp_path / "live.jsonl"
        _zone(landing, 400)
        database = _FakeDatabase()
        database.install(monkeypatch, keep_rows=True)

        _load(landing, _seed_file(tmp_path), tmp_path, "--window", "20000")

        # What the database ends up holding, last write winning per key.
        final: dict[tuple[str, str], Assignment] = {}
        for row in database.assignment_rows:
            final[(row["asta_id"], row["player_uuid"])] = Assignment(
                auction_id=row["asta_id"],
                player_id=row["player_uuid"],
                price=row["price"],
                buyer_team_id=row["buyer_team_id"],
                closed_at_ms=row["closed_at_ms"],
                ladder=tuple(
                    Bid(price=b["price"], team_id=b["team_id"], at_ms=b["at_ms"])
                    for b in row["ladder"]
                ),
            )

        whole = reconstruct(iter_records(landing))
        assert whole, "the fixture produced no sales; this test would prove nothing"
        verdict = equivalent(whole, list(final.values()))
        assert verdict.ok, verdict.reason

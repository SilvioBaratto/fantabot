"""Storing readings as they arrive, so a crash costs the in-flight ones only.

`news fetch --write` gathered all 548 players and issued **one** upsert after
the last of them returned. Two things followed from that, both observed on
2026-08-28:

* `player_sentiment` read 0 for the whole run, so there was no way to tell a
  working run from a stalled one without reading the source.
* The run was all-or-nothing. Measured that morning at roughly two players a
  minute, that is about 1 h 50 m during which a crash, a Ctrl-C or a closed lid
  discards every query. The resume filter existed and could never help, because
  nothing was ever stored for it to resume from.

The sink is the fix and is deliberately dumb: it batches, it flushes through an
injected callable, and it **keeps rows that failed to flush** rather than
dropping them — `harvest load`'s rule, that an outage costs time and never a
record, applied to the other long-running command.

It also de-duplicates by key, which is what lets the command keep its
end-of-run pass over `result.rows`: the incremental path is the crash-safety,
the final pass is still the guarantee of completeness, and a row that went
through both is stored once.
"""

from __future__ import annotations

from typing import Any

import pytest

from fantabot.news.sink import SentimentSink


def _row(player_id: str, day: str = "2026-08-28") -> dict[str, str]:
    return {"data_run": day, "id": player_id, "nome": f"P{player_id}"}


class _Flush:
    """A flush that records what it was handed, and can be told to fail."""

    def __init__(self, fail_times: int = 0) -> None:
        self.batches: list[list[dict[str, str]]] = []
        self._fail_times = fail_times

    def __call__(self, rows: list[dict[str, str]]) -> int:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("database unreachable")
        self.batches.append(list(rows))
        return len(rows)


class TestItFlushesInBatches:
    def test_nothing_leaves_before_the_batch_is_full(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=3)

        sink.add(_row("1"))
        sink.add(_row("2"))

        assert flush.batches == []
        assert sink.pending == 2
        assert sink.stored == 0

    def test_a_full_batch_goes_out_on_its_own(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=3)

        for i in range(3):
            sink.add(_row(str(i)))

        assert [len(batch) for batch in flush.batches] == [3]
        assert sink.pending == 0
        assert sink.stored == 3

    def test_draining_sends_the_partial_batch(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=10)
        sink.add(_row("1"))

        assert sink.drain() == 1
        assert sink.stored == 1
        assert sink.pending == 0

    def test_draining_an_empty_sink_issues_no_flush(self) -> None:
        """Matching `upsert_rows`, which opens no transaction for an empty batch."""
        flush = _Flush()
        sink = SentimentSink(flush, every=10)

        assert sink.drain() == 0
        assert flush.batches == []

    def test_every_below_one_still_flushes_each_row(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=0)

        sink.add(_row("1"))

        assert sink.stored == 1


class TestAFailedFlushKeepsItsRows:
    """The property that makes this safe to put on the hot path.

    A sink that dropped a failed batch would turn a ten-second database blip
    into five silently missing players — the shape of loss this repo has had to
    name three times already.
    """

    def test_rows_survive_a_flush_that_raised(self) -> None:
        flush = _Flush(fail_times=1)
        sink = SentimentSink(flush, every=2)

        sink.add(_row("1"))
        sink.add(_row("2"))

        assert flush.batches == []
        assert sink.pending == 2
        assert sink.stored == 0
        assert sink.flush_failures == 1

    def test_the_next_row_retries_the_batch_that_failed(self) -> None:
        """Held rows keep the batch over its size, so the retry is the very next
        row rather than a wait until the run ends."""
        flush = _Flush(fail_times=1)
        sink = SentimentSink(flush, every=2)

        sink.add(_row("1"))
        sink.add(_row("2"))  # batch full: this flush fails, and both are kept
        sink.add(_row("3"))  # still over the batch size, so all three go now
        sink.add(_row("4"))
        sink.drain()

        assert [len(batch) for batch in flush.batches] == [3, 1]
        assert [r["id"] for r in flush.batches[0]] == ["1", "2", "3"]
        assert sink.stored == 4
        assert sink.pending == 0

    def test_a_failure_is_counted_rather_than_raised(self) -> None:
        sink = SentimentSink(_Flush(fail_times=5), every=1)

        sink.add(_row("1"))

        assert sink.flush_failures == 1
        assert sink.pending == 1

    def test_rows_still_pending_after_a_final_drain_are_visible(self) -> None:
        """The command exits non-zero on this; it must be able to see it."""
        sink = SentimentSink(_Flush(fail_times=99), every=10)
        sink.add(_row("1"))

        assert sink.drain() == 0
        assert sink.pending == 1


class TestTheEndOfRunPassIsFree:
    """`news fetch` stores each row as it lands *and* passes `result.rows` at the
    end, so a bug in the incremental path cannot lose the run. The second pass
    must therefore cost nothing when the first one already did the work."""

    def test_a_row_added_twice_is_stored_once(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=10)

        sink.add(_row("1"))
        sink.add(_row("1"))
        sink.drain()

        assert [r["id"] for r in flush.batches[0]] == ["1"]

    def test_extend_skips_what_was_already_stored(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=2)

        sink.add(_row("1"))
        sink.add(_row("2"))  # flushed
        sink.extend([_row("1"), _row("2"), _row("3")])
        sink.drain()

        assert [len(batch) for batch in flush.batches] == [2, 1]
        assert [r["id"] for r in flush.batches[1]] == ["3"]
        assert sink.stored == 3

    def test_the_same_player_on_a_different_day_is_a_different_row(self) -> None:
        flush = _Flush()
        sink = SentimentSink(flush, every=10)

        sink.add(_row("1", day="2026-08-28"))
        sink.add(_row("1", day="2026-09-04"))
        sink.drain()

        assert len(flush.batches[0]) == 2

    def test_a_row_missing_its_key_is_refused_rather_than_stored_unkeyed(self) -> None:
        """Silently accepting it would let the same reading be stored twice."""
        sink = SentimentSink(_Flush(), every=10)

        with pytest.raises(KeyError):
            sink.add({"nome": "no key here"})


class TestItReportsWhatItDid:
    def test_stored_counts_what_the_flush_reported_not_what_was_offered(self) -> None:
        """`upsert_rows` is DO NOTHING without --force, so a row that was already
        in the table is sent and not inserted. The count that matters downstream
        is the one the repository returns."""

        def flush(rows: list[dict[str, Any]]) -> int:
            return 0

        sink = SentimentSink(flush, every=1)
        sink.add(_row("1"))

        assert sink.stored == 0
        assert sink.pending == 0, "a successful flush must clear its rows either way"


class TestAFailingFlushSpeaks:
    """`harvest load` prints `database unreachable: OperationalError` the moment a
    pass cannot write. This had nothing — the only signal was the ` · N stored`
    fragment of the progress line quietly ceasing to advance while the counter,
    the sentiment and the ETA all kept looking healthy. On a run measured at
    four hours that is a long time to be told nothing.
    """

    def test_the_first_failure_is_handed_to_the_caller(self) -> None:
        seen: list[str] = []
        sink = SentimentSink(_Flush(fail_times=1), every=1, on_error=lambda e: seen.append(str(e)))

        sink.add(_row("1"))

        assert seen == ["database unreachable"]

    def test_every_failure_is_reported_not_only_the_first(self) -> None:
        seen: list[Exception] = []
        sink = SentimentSink(_Flush(fail_times=3), every=1, on_error=seen.append)

        for i in range(3):
            sink.add(_row(str(i)))

        assert len(seen) == 3
        assert all(isinstance(exc, RuntimeError) for exc in seen)

    def test_a_successful_flush_says_nothing(self) -> None:
        seen: list[Exception] = []
        sink = SentimentSink(_Flush(), every=1, on_error=seen.append)

        sink.add(_row("1"))

        assert seen == []

    def test_the_hook_is_optional(self) -> None:
        sink = SentimentSink(_Flush(fail_times=1), every=1)

        sink.add(_row("1"))

        assert sink.flush_failures == 1

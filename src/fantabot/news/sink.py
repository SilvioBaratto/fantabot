"""Storing readings as they land, rather than all of them at the end.

``news fetch --write`` gathered every player and then issued **one** upsert. Two
consequences, both observed on 2026-08-28 against a 548-player pool running at
roughly two players a minute:

* ``player_sentiment`` read ``0`` for the whole run. There was no way to tell a
  working run from a stalled one without reading the source.
* The run was all-or-nothing for nearly two hours. A crash, a Ctrl-C or a closed
  lid discarded every query — and the resume filter that exists precisely for
  this could never help, because nothing had been stored for it to resume from.

This sink is deliberately dumb. It batches, it flushes through an injected
callable, and it **keeps rows whose flush failed** instead of dropping them:
``harvest load``'s rule — an outage costs catch-up time and never a record —
applied to the other long-running command. A ten-second database blip must not
turn into five players silently missing from the week.

It de-duplicates by ``(data_run, id)``, which is what lets the command keep its
end-of-run pass over ``result.rows``. The incremental path is the crash-safety;
the final pass stays the guarantee of completeness. A row that goes through both
is stored once, and a bug in the first cannot lose the run.

No database import here. The flush is passed in, so the whole thing is testable
with a list — the same reason ``pipeline.py`` takes its runner.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

#: What a flush is handed, and what it reports back: rows sent, rows stored.
Flush = Callable[[list[dict[str, str]]], int]


class SentimentSink:
    """Batches finished readings and flushes them as the batch fills."""

    def __init__(
        self,
        flush: Flush,
        *,
        every: int = 5,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._flush = flush
        self._on_error = on_error
        #: A batch of zero would never fill, so the floor is one row per flush.
        self._every = max(1, every)
        self._pending: list[dict[str, str]] = []
        self._seen: set[tuple[str, str]] = set()
        self.stored = 0
        self.flush_failures = 0

    @property
    def pending(self) -> int:
        """Rows held and not yet stored — non-zero at exit means loss."""
        return len(self._pending)

    def add(self, row: Mapping[str, str]) -> None:
        """Queue one reading, flushing if that fills the batch.

        A row without its key is refused rather than queued unkeyed: accepting
        it would let the same reading be stored twice, which is the failure the
        primary key exists to prevent.
        """
        key = (row["data_run"], row["id"])
        if key in self._seen:
            return
        self._seen.add(key)
        self._pending.append(dict(row))
        if len(self._pending) >= self._every:
            self.drain()

    def extend(self, rows: Iterable[Mapping[str, str]]) -> None:
        """Queue many, skipping any this sink has already taken."""
        for row in rows:
            self.add(row)

    def drain(self) -> int:
        """Flush what is held. Returns rows stored; keeps them on failure.

        Issues no statement for an empty batch, matching ``upsert_rows``, so a
        pass with nothing to say opens no transaction.

        A failed flush is counted and the rows stay queued for the next attempt.
        Raising here would end a run over a transient outage; dropping them
        would lose a batch nobody counted, which is the same shape of loss three
        times over in ``aste/``.
        """
        if not self._pending:
            return 0
        try:
            stored = self._flush(self._pending)
        except Exception as exc:
            self.flush_failures += 1
            if self._on_error is not None:
                # Said out loud, the way `harvest load` names an unreachable
                # database. The only other sign is the progress line's stored
                # count ceasing to advance while every other field on it — the
                # counter, the scores, the ETA — goes on looking healthy.
                self._on_error(exc)
            return 0
        self._pending.clear()
        self.stored += stored
        return stored

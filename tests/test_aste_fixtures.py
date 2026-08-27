"""The recorded corpus the SSE parser and reducer will be tested against.

These assertions are about the *fixtures*, not about any code that reads them.
That ordering is deliberate: the parser (T6) is verified by replaying recorded
bytes, so a corpus that quietly lacks the awkward cases produces a parser that
passes and a collector that loses data. The corpus is the thing under test here.

Two formats live side by side and must not be confused — the first draft of the
plan did confuse them, and the resulting acceptance criterion proved nothing:

* ``sse/`` holds **raw SSE bytes**, exactly as Firebase writes them. Only the
  live path produces these.
* ``states/`` holds **merged states**, the shape ``data/aste_live/*.jsonl``
  already contains. The backfill path consumes these and never sees a frame.

Fixtures marked ``synthetic`` are hand-written because a short recording is not
guaranteed to contain them; every other file is a verbatim capture.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SSE = FIXTURES / "sse"
STATES = FIXTURES / "states"


def _read(name: str) -> str:
    return (SSE / name).read_text(encoding="utf-8")


def test_sse_corpus_is_present() -> None:
    assert SSE.is_dir(), "no tests/fixtures/sse — record it before writing the parser"
    assert list(SSE.glob("*.txt")), "the SSE corpus is empty"


def test_corpus_covers_the_initial_snapshot() -> None:
    """``put`` carries the whole node; without it a reconnect has no base state."""
    assert "event: put" in _read("live_auction.txt")


def test_corpus_covers_incremental_patches() -> None:
    assert "event: patch" in _read("live_auction.txt")


def test_corpus_covers_a_null_valued_patch() -> None:
    """A ``null`` patch value deletes the key. Storing it instead leaves a stale
    price on the board forever, which is the failure this fixture exists to catch."""
    frames = _read("null_patch.txt")
    assert "event: patch" in frames
    payload = json.loads(frames.split("data: ", 1)[1].splitlines()[0])
    assert any(v is None for v in payload["data"].values())


def test_corpus_covers_a_frame_split_across_chunks() -> None:
    """Transport chunk boundaries fall anywhere. A parser that assumes one frame
    per read silently drops the tail."""
    chunks = json.loads((SSE / "split_frame.json").read_text(encoding="utf-8"))
    assert len(chunks) >= 2
    joined = "".join(chunks)
    assert "event: " in joined and "data: " in joined
    assert not chunks[0].endswith("\n\n"), "the first chunk must cut a frame in half"


def test_corpus_covers_a_keepalive() -> None:
    """Firebase's keep-alive is a real event, not the SSE comment line the spec
    convention would suggest. This assertion was written the other way round and
    the recording disproved it — which is the reason fixtures are captured rather
    than imagined."""
    frames = _read("keepalive.txt")
    assert "event: keep-alive" in frames
    assert "data: null" in frames


def test_states_fixture_is_one_complete_auction() -> None:
    rows = [json.loads(line) for line in (STATES / "one_auction.jsonl").read_text().splitlines()]
    assert rows, "the states fixture is empty"
    assert len({r["auction_id"] for r in rows}) == 1, "more than one auction in the fixture"
    kinds = {r["state"].get("update_type") for r in rows}
    assert "close_auction" in kinds, "no assignment in the fixture — nothing to reconstruct"

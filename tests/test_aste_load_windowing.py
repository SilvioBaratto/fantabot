"""`aste-load` must not store a ladder shorter than the file supports.

The loader reads incrementally, which is right for events and wrong for
assignments: `reconstruct` holds its ladders in locals, so a window that starts
mid-turn rebuilds from nothing. The upsert is DO UPDATE, so the short ladder
then overwrites the complete one, and the checkpoint never comes back for the
rungs it skipped.

That defeats the phase's whole point silently — the sale is there, the price is
right, only the ladder is short — and it happens on the documented live path
(`aste-collect &` + `aste-load --follow`).
"""

from __future__ import annotations

import json
from pathlib import Path

from fantabot.aste.backfill import build, read_jsonl
from fantabot.aste.loader import Checkpoint, read_from

SEED = [["a-1", "4", 8, 500, 25, 25, "random", "free", 8, 8, "L", "mantra"]]

TURN = [
    {"update_type": "first_call", "player_id": "p", "price": 0, "last_update": 1},
    {"update_type": "raise", "player_id": "p", "price": 1, "last_update": 2,
     "fantateam_id": "t1"},
    {"update_type": "raise", "player_id": "p", "price": 2, "last_update": 3,
     "fantateam_id": "t2"},
    {"update_type": "raise", "player_id": "p", "price": 3, "last_update": 4,
     "fantateam_id": "t1"},
    {"update_type": "close_auction", "player_id": "p", "price": 3, "last_update": 5,
     "fantateam_id": "t1"},
]


def _write(path: Path, states: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for i, state in enumerate(states):
            handle.write(json.dumps(
                {"seen_at": f"2026-08-27T20:00:{i:02d}+00:00",
                 "auction_id": "a-1", "state": state}) + "\n")


def _ladder(rows: list[dict]) -> list[int]:
    assignments = build(rows, SEED, {}, "mantra").assignments
    return [rung["price"] for rung in assignments[0]["ladder"]] if assignments else []


def test_one_pass_over_the_whole_file_keeps_the_ladder(tmp_path: Path) -> None:
    landing = tmp_path / "live.jsonl"
    _write(landing, TURN)
    assert _ladder(read_jsonl(landing)) == [0, 1, 2, 3]


def test_two_passes_must_not_shorten_it(tmp_path: Path) -> None:
    """The exact shape `--follow` produces: the collector appends while the
    loader reads, so a turn is split across passes. Whatever the loader stores
    after the last pass must equal what a single whole-file pass stores."""
    from fantabot.aste.loader import assignments_for_pass

    landing = tmp_path / "live.jsonl"
    checkpoint = Checkpoint(landing)

    _write(landing, TURN[:3])
    first_records, offset = read_from(landing, checkpoint.read())
    ladder_one = assignments_for_pass(landing, first_records)
    checkpoint.write(offset)

    _write(landing, TURN[3:])
    second_records, offset = read_from(landing, checkpoint.read())
    ladder_two = assignments_for_pass(landing, second_records)
    checkpoint.write(offset)

    stored = ladder_two or ladder_one
    ladder = [rung["price"] for rung in stored[0]["ladder"]] if stored else []
    assert ladder == [0, 1, 2, 3], (
        "the second pass overwrote the complete ladder with the rungs it happened to see"
    )


def test_events_stay_incremental(tmp_path: Path) -> None:
    """Assignments need the whole file; events must not re-read it, or every
    pass re-uploads the entire evening."""
    landing = tmp_path / "live.jsonl"
    checkpoint = Checkpoint(landing)
    _write(landing, TURN[:3])
    first, offset = read_from(landing, checkpoint.read())
    checkpoint.write(offset)
    _write(landing, TURN[3:])
    second, _ = read_from(landing, checkpoint.read())
    assert len(first) == 3
    assert len(second) == 2, "the second pass must carry only what is new"

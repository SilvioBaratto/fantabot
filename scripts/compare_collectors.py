"""T16 — read a shadow run and say whether streaming is a strict superset.

Both captures are collector logs of the same rooms, so both go through the same
``reconstruct``. Nothing here reimplements it: if the comparison had its own
reading of a state, a bug in that reading would be indistinguishable from a bug
in the collector it is judging.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fantabot.aste.backfill import read_jsonl
from fantabot.aste.compare import compare, observation_window
from fantabot.aste.reconstruct import reconstruct


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--polled", type=Path, required=True)
    ap.add_argument("--streamed", type=Path, required=True)
    args = ap.parse_args()

    polled_rows = read_jsonl(args.polled)
    streamed_rows = read_jsonl(args.streamed)
    polled = reconstruct(polled_rows)
    streamed = reconstruct(streamed_rows)
    # From when each side was watching, not from when sales closed: a node keeps
    # returning a closed state, so either collector can see a sale older than its
    # own connection.
    window = observation_window(polled_rows, streamed_rows)
    print(f"polled   {len(polled):>5} sales")
    print(f"streamed {len(streamed):>5} sales")

    verdict = compare(polled, streamed, window=window)
    print(verdict.summary())
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

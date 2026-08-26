"""Merge newly-seen live Mantra auctions into the collector's seed file.

The list of public auctions never crosses the wire as JSON — it arrives over
the Firebase WebSocket the SPA opens — so it is read back out of the rendered
React tree. Each card's props carry the auction's whole configuration, ``db``
(the Firebase shard) included, which is what the collector needs to address it.

**The list requires a signed-in session.** A fresh headless browser is bounced
to ``/signup``; only the ``auction/<id>`` Firebase node is public. So the page
is driven through the operator's already-authenticated Chrome and the extracted
rows are piped in here — no credentials are ever handled by this code. The
JavaScript that does the extraction is ``EXTRACT`` below, kept here so the two
halves stay in one file.

Merges rather than replaces: an auction that has already ended still belongs in
the record of what we watched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIST_URL = "https://app.fantalab.it/aste-live"

# Walks every fiber on the page and keeps any props object that looks like an
# auction record. Cheap, and immune to class-name churn in the bundle.
EXTRACT = """
() => {
  const fk = el => Object.keys(el).find(k => k.startsWith('__reactFiber$'));
  const by = new Map();
  for (const el of document.querySelectorAll('*')) {
    const k = fk(el); if (!k) continue;
    let f = el[k], d = 0;
    while (f && d++ < 8) {
      const p = f.memoizedProps;
      if (p && typeof p === 'object') {
        for (const v of Object.values(p)) {
          if (v && typeof v === 'object' && !Array.isArray(v)
              && typeof v.fantaleague_id === 'string' && 'db' in v) {
            by.set(v.fantaleague_id, v);
          }
        }
      }
      f = f.return;
    }
  }
  return [...by.values()].filter(a => a.asta_type === 'mantra').map(a => [
    a.fantaleague_id, String(a.db), a.num_teams, a.num_credits,
    a.min_player, a.max_player, a.asta_mode, a.raise_mode,
    a.counter_time, a.counter_time_first, a.fantaleague_name
  ]);
}
"""


def merge(seed: Path, found: list[list]) -> tuple[int, int, list[str]]:
    existing = json.loads(seed.read_text(encoding="utf-8")) if seed.exists() else []
    known = {r[0] for r in existing}
    fresh = [r for r in found if r[0] not in known]
    if fresh:
        seed.write_text(
            json.dumps(existing + fresh, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )
    return len(existing), len(found), [r[0] for r in fresh]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument(
        "--rows",
        type=Path,
        help="JSON array of extracted rows; omit to read them from stdin",
    )
    args = ap.parse_args()

    raw = args.rows.read_text(encoding="utf-8") if args.rows else sys.stdin.read()
    found = json.loads(raw)
    before, seen, fresh = merge(args.seed, found)
    print(f"seed {before} -> {before + len(fresh)}   mantra live now: {seen}   new: {len(fresh)}")
    for fid in fresh:
        print(f"  + {fid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

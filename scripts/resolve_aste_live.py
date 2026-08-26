"""Resolve collected FantaLab auction events into named assignments.

The live stream identifies players by FantaLab UUID. ``GET /v2/listone`` maps
those to names — and, decisively, to ``fantacalcio_id``, which is the same
integer key our ``players`` table already uses. So the bridge is an exact join,
not a fuzzy name match.

Both the listone and the auction nodes are readable without authentication;
only the *list* of live auctions sits behind a session (see ``scan_aste_live``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

LISTONE = "https://api.fantalab.it/v2/listone"


def fetch_listone(cache: Path | None) -> dict[str, dict]:
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    payload = httpx.get(LISTONE, timeout=30).json()
    table = {
        p["player_id"]: {
            "fantacalcio_id": p.get("fantacalcio_id"),
            "name": p.get("name"),
            "team": p.get("team_name"),
            "role": p.get("role"),
            "mantra_roles": p.get("mantra_roles"),
            "quotazione_mantra": p.get("quotazione_mantra"),
            "fvm_mantra": p.get("fvm_mantra"),
        }
        for p in payload["players"]
    }
    if cache:
        cache.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    return table


def assignments(events: Path, seed: Path, listone: dict[str, dict]) -> list[dict]:
    """One row per (auction, player) that reached ``close_auction``."""
    leagues = {r[0]: r for r in json.loads(seed.read_text(encoding="utf-8"))}
    out: dict[tuple[str, str], dict] = {}
    with events.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            s = row["state"]
            if s.get("update_type") != "close_auction":
                continue
            pid, price = s.get("player_id"), s.get("price")
            if not pid or price is None:
                continue
            lg = leagues.get(row["auction_id"], [None] * 11)
            p = listone.get(pid, {})
            # First close wins: a repeat is the node still holding the last state.
            out.setdefault(
                (row["auction_id"], pid),
                {
                    "auction_id": row["auction_id"],
                    "league_name": lg[-1],
                    "num_teams": lg[2],
                    "num_credits": lg[3],
                    "asta_mode": lg[6],
                    "closed_at": row["seen_at"],
                    "price": price,
                    "buyer_team_id": s.get("fantateam_id"),
                    "player_id": pid,
                    "fantacalcio_id": p.get("fantacalcio_id"),
                    "name": p.get("name"),
                    "team": p.get("team"),
                    "mantra_roles": p.get("mantra_roles"),
                    "quotazione_mantra": p.get("quotazione_mantra"),
                },
            )
    return list(out.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", type=Path, required=True)
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument("--listone-cache", type=Path, default=Path("data/aste_live/listone_map.json"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    rows = assignments(args.events, args.seed, fetch_listone(args.listone_cache))
    rows.sort(key=lambda r: -r["price"])
    if args.out:
        args.out.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
    print(f"{len(rows)} assignments")
    unmatched = sum(1 for r in rows if r["fantacalcio_id"] is None)
    if unmatched:
        print(f"  {unmatched} without a fantacalcio_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Poll FantaLab's public spectator state for live Mantra auctions.

The auction room keeps its live state in a Firebase RTDB node that spectator
mode reads without authentication:

    https://fantalab-<db>.europe-west1.firebasedatabase.app/auction/<id>.json

Every distinct state is appended to a JSONL file. Nothing is interpreted here:
the reconstruction of assignments from ``update_type`` transitions happens
offline, against the raw log, because a parser bug must never cost us data we
cannot collect twice.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

DEFAULT_NS = "fantalab-79eaa-default-rtdb"


def namespaces(db: str) -> list[str]:
    """Candidate hosts for a shard id, most likely first."""
    return [f"fantalab-{db}", DEFAULT_NS] if db != "0" else [DEFAULT_NS, "fantalab-0"]


def url(ns: str, auction_id: str) -> str:
    return f"https://{ns}.europe-west1.firebasedatabase.app/auction/{auction_id}.json"


async def resolve(client: httpx.AsyncClient, auction_id: str, db: str) -> str | None:
    """Find which namespace actually serves this auction."""
    for ns in namespaces(db):
        try:
            r = await client.get(url(ns, auction_id), timeout=10)
            if r.status_code == 200 and r.json() is not None:
                return ns
        except Exception:  # a bad gateway page is not JSON
            continue
    return None


async def watch(
    client: httpx.AsyncClient,
    auction_id: str,
    db: str,
    meta: dict,
    out: Path,
    interval: float,
    lock: asyncio.Lock,
    stop: asyncio.Event,
) -> None:
    ns = await resolve(client, auction_id, db)
    if ns is None:
        print(f"  unresolved  {auction_id}  db={db}", file=sys.stderr)
        return
    print(f"  watching    {auction_id}  {ns}  {meta.get('name')}", file=sys.stderr)

    last: str | None = None
    misses = 0
    while not stop.is_set():
        try:
            r = await client.get(url(ns, auction_id), timeout=10)
            state = r.json() if r.status_code == 200 else None
        except Exception:  # never let one bad body end the watch
            state = None

        if state is None:
            misses += 1
            if misses > 40:  # ~2 minutes of nothing: the room is over
                print(f"  ended       {auction_id}", file=sys.stderr)
                return
        else:
            misses = 0
            key = json.dumps(state, sort_keys=True)
            if key != last:
                last = key
                row = {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "auction_id": auction_id,
                    "ns": ns,
                    "state": state,
                }
                async with lock:
                    with out.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--minutes", type=float, default=0.0, help="0 = run until killed")
    ap.add_argument("--rescan", type=float, default=60.0, help="seconds between seed reloads")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    lock = asyncio.Lock()
    stop = asyncio.Event()
    limits = httpx.Limits(max_connections=40, max_keepalive_connections=40)
    watching: dict[str, asyncio.Task] = {}

    async with httpx.AsyncClient(limits=limits, http2=False) as client:

        def spawn(rows: list) -> int:
            """Start a watcher for every auction we are not already following."""
            added = 0
            for r in rows:
                if r[0] in watching:
                    continue
                watching[r[0]] = asyncio.create_task(
                    watch(
                        client,
                        r[0],
                        r[1],
                        {"teams": r[2], "credits": r[3], "name": r[-1]},
                        args.out,
                        args.interval,
                        lock,
                        stop,
                    )
                )
                added += 1
            return added

        spawn(json.loads(args.seed.read_text(encoding="utf-8")))
        elapsed = 0.0
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=args.rescan)
            elapsed += args.rescan
            # The scan script merges new auctions into the seed; pick them up
            # without a restart, because a restart re-emits every current state.
            # A watcher that raised is gone until we notice. Drop it from the
            # registry so the seed reload below starts it again.
            for aid, task in list(watching.items()):
                if task.done() and task.exception() is not None:
                    print(f"  ! watcher crashed, respawning {aid}: "
                          f"{task.exception()!r}", file=sys.stderr)
                    del watching[aid]
            try:
                added = spawn(json.loads(args.seed.read_text(encoding="utf-8")))
                if added:
                    print(f"  + {added} auction(s) started", file=sys.stderr)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  seed reload failed: {exc}", file=sys.stderr)
            live = sum(1 for t in watching.values() if not t.done())
            print(f"  heartbeat  {live} live / {len(watching)} seen", file=sys.stderr)
            if args.minutes and elapsed >= args.minutes * 60:
                stop.set()

        await asyncio.gather(*watching.values(), return_exceptions=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

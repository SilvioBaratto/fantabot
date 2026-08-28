"""The harvest commands: scan, collect, load, backfill — and FantaLab sign-in.

Lifted out of ``cli.py``, which had grown to 951 lines with two thirds of the
growth from this one phase. They belong here for a better reason than size: the
rest of the CLI drives *our* leagues on leghe.fantacalcio.it, while these five
read a different site for training data, and nothing is shared between the two
but the console.

Registered rather than decorated, because ``app`` lives in ``cli.py`` and
importing it back would close the circle. ``register`` runs last there, so
these five now list together at the end of ``--help`` rather than interleaved
with the league commands — the one visible difference the move makes.

**Import-light, like its parent.** Every body imports what it needs when it
runs. ``cli.py`` imports this module at start-up, so a module-level
``sqlalchemy`` or ``playwright`` here would land in every ``fantabot --help``
— which a test in ``test_db_boundary.py`` refuses.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

if TYPE_CHECKING:  # annotations only
    from fantabot.aste.backfill import DroppedEvents

console = Console()


def aste_scan(
    seed: Path = typer.Option(..., help="Registry file to merge into and rewrite."),
    only: str = typer.Option("", help="Keep one format: mantra or classic. Empty = both."),
) -> None:
    """Ask FantaLab which auctions are live and merge them into the registry.

    Replaces walking the page's React tree with one authenticated GET — spike S2.
    Both formats are fetched: filtering is a query, never a decision taken at
    collection time, and the poller filtering to Mantra is what threw away 85%
    of the population.
    """
    import json

    from fantabot.aste.client import AuthExpired, LiveAuctionsClient, ScanEmpty
    from fantabot.aste.registry import from_seed_row, merge, to_seed_rows
    from fantabot.config import settings
    from fantabot.db import database_manager
    from fantabot.tokens.crypto import TokenCipher
    from fantabot.tokens.fantalab_store import FantalabStore

    cipher = TokenCipher(settings.fantabot_encryption_key)
    with database_manager.get_session() as session:
        stored = FantalabStore(session, cipher).load()
    if stored is None or not stored.id_token:
        console.print("[red]No FantaLab session stored. Run: fantabot fantalab-login[/red]")
        raise typer.Exit(2)

    try:
        scanned = LiveAuctionsClient(stored.id_token).live_auctions()
    except (AuthExpired, ScanEmpty) as exc:
        # Both are refusals, not empty results. Reporting zero here would look
        # exactly like a quiet night and the next scan would never be run.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if only:
        scanned = [c for c in scanned if c.asta_type == only]

    known = []
    if seed.exists():
        # The legacy file predates storing the format; everything in it was Mantra.
        known = [from_seed_row(row, asta_type="mantra")
                 for row in json.loads(seed.read_text(encoding="utf-8"))]

    merged = merge(known, scanned)
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(
        json.dumps(to_seed_rows(merged), ensure_ascii=False, indent=0) + "\n",
        encoding="utf-8",
    )

    added = len(merged) - len(known)
    formats: dict[str, int] = {}
    for config in scanned:
        formats[config.asta_type] = formats.get(config.asta_type, 0) + 1
    console.print(
        f"live {len(scanned)} ({', '.join(f'{k} {v}' for k, v in sorted(formats.items()))})"
        f" · registry {len(known)} -> {len(merged)} (+{added})"
    )


def fantalab_login(
    force: bool = typer.Option(False, "--force", help="Re-authenticate even if a session exists."),
    browser: str = typer.Option(
        "", help="Installed browser to drive: msedge, chrome. Empty = bundled Chromium."
    ),
) -> None:
    """Sign in to FantaLab once; store the session encrypted in Postgres.

    Opens a real browser and waits. **This program types nothing and clicks
    nothing** — a scripted sign-in is what gets accounts flagged, and FantaLab
    offers Google and Apple besides its own form, so there is no single flow to
    automate even if that were wanted.

    No `storage_state.json` is written. That file would hold three credentials
    in the clear; they go from browser memory through Fernet into Postgres.
    """
    from fantabot.fantalab_login import LoginAborted
    from fantabot.fantalab_login import run as run_login

    try:
        run_login(force=force, channel=browser or None)
    except LoginAborted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(exc.code) from None


def _report_dropped(dropped: DroppedEvents) -> None:
    """Say what did not become an event row.

    Only when something did: a zero line every pass is noise nobody reads, and
    noise nobody reads is how the non-zero one gets missed. An unknown auction
    is routine — the collector follows rooms the seed does not describe — so it
    is reported at a lower key than a record that was simply broken.
    """
    if not dropped.any:
        return
    colour = "yellow" if dropped.malformed_state or dropped.bad_timestamp else "dim"
    console.print(f"[{colour}]{dropped.total} record(s) dropped — {dropped.summary()}[/{colour}]")


def aste_load(
    landing: Path = typer.Argument(..., help="Landing-zone JSONL the collector appends to."),
    seed: Path = typer.Option(..., help="The scan seed describing each auction."),
    listone: Path = typer.Option(
        Path("data/aste_live/listone_map.json"),
        help="uuid -> fantacalcio_id bridge from GET /v2/listone.",
    ),
    asta_type: str = typer.Option("mantra", help="Format the seed was collected for."),
    follow: bool = typer.Option(False, "--follow", help="Keep reading as the file grows."),
    interval: float = typer.Option(10.0, help="Seconds between passes when following."),
    window: int = typer.Option(0, help="Bytes one pass carries. 0 = the default window."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Read and report, write nothing."),
) -> None:
    """Carry the landing zone into Postgres, resuming where the last pass stopped.

    The database is deliberately not on the collection critical path: the
    collector writes to the file and this reads from it. An outage costs
    catch-up time, never a record.

    A pass reads at most ``--window`` bytes, so carrying a backlog is several
    passes; the command makes them all before returning. ``--follow`` adds only
    watching the file after that. A dry run makes one pass and stops, because
    its checkpoint never moves and looping would re-read one window for ever.
    """
    import json
    import time

    from fantabot.aste.backfill import auction_rows, event_rows
    from fantabot.aste.loader import (
        DEFAULT_WINDOW_BYTES,
        CachedPlayerIds,
        Checkpoint,
        LandingZoneMissing,
        SeedRows,
        assignments_for_pass,
        catching_up,
        read_from,
    )
    from fantabot.db.models.aste import ASTA_TYPES

    if asta_type not in ASTA_TYPES:
        console.print(f"[red]{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}")
        raise typer.Exit(2)
    if not seed.exists():
        console.print(f"[red]seed file not found: {seed}[/red]")
        raise typer.Exit(2)

    bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    # One pass holds its window several times over. Uncapped, the cost of a pass
    # grows with how far behind the loader is — so the further it falls, the less
    # able it is to start, and a 1.14 GB backlog could not be loaded at all.
    pass_window = window or DEFAULT_WINDOW_BYTES
    checkpoint = Checkpoint(landing)
    # Re-read every pass, not once: the collector adopts auctions that open
    # after it started, and a loader holding the startup seed calls their events
    # unknown and advances its checkpoint past them.
    seed_source = SeedRows(seed)
    # `players`, by contrast, only moves when someone runs `db-import`, and
    # reading it is a session and 1,492 ids across the wire.

    def fetch_known_players() -> frozenset[int]:
        from fantabot.db import database_manager
        from fantabot.db.repositories.aste import AsteRepository

        with database_manager.get_session() as session:
            return AsteRepository(session).known_player_ids()

    player_cache = CachedPlayerIds(fetch_known_players)

    # What a one-shot run is carrying: the landing zone as it stood when the
    # command started. Bounded rather than "until nothing is behind", because
    # the collector is still appending — an unbounded one-shot run against a
    # live evening would never return. `--follow` has no target; it stops
    # hurrying once less than a window is owed and sleeps instead.
    target = None if follow else (landing.stat().st_size if landing.exists() else 0)

    def pass_once() -> tuple[int, int, bool]:
        """Records carried, bytes still behind the writer, and whether the
        ladder rebuild was deferred — which is also the answer to *is another
        pass coming*, and the only thing the loop below needs to know."""
        offset = checkpoint.read()
        records, new_offset = read_from(landing, offset, max_bytes=pass_window)
        size = landing.stat().st_size if landing.exists() else new_offset
        behind = max(0, size - new_offset)
        if not records:
            # The real lag, not zero. A window that parsed nothing is not a
            # caught-up loader, and reporting it as one is the mistake this
            # command already had to stop making about a missing landing zone.
            return 0, behind, False

        # "Another pass is coming" — the one value the loop's `continue` tests
        # and the one that decides whether the ladders can wait. Two conditions
        # would drift, and the drift would leave the ladders behind the events.
        # A dry run never has one: its checkpoint does not move, so a second
        # pass would re-read the same window for ever.
        deferring = not dry_run and (
            new_offset < target
            if target is not None
            else catching_up(behind, window=pass_window)
        )

        known_players = None if dry_run else player_cache.get(now=time.monotonic())
        auctions = auction_rows(seed_source.read(), asta_type)
        known = {row["id"] for row in auctions}

        # Events from the window: they are append-only, so re-reading would
        # re-upload the whole evening every pass.
        events, dropped = event_rows(records, known)
        # Assignments from the WHOLE landing zone. A window that starts mid-turn
        # rebuilds a ladder from nothing, and the upsert is DO UPDATE — the short
        # ladder would overwrite the complete one and the checkpoint would never
        # come back for the rungs it skipped.
        #
        # Which is exactly why a pass in the middle of a catch-up need not do it:
        # the pass that lands rebuilds from the whole file anyway, including
        # every record the deferred passes carried. Measured on the 2026-08-28
        # backlog, paying it per window cost 9.4 s and ~880 MB thirty-four times
        # over, plus ~160,000 rows re-upserted each time, for a result thrown
        # away by the next pass. Events stay incremental — they are append-only,
        # so re-reading them would re-upload the evening.
        assignments = (
            []
            if deferring
            else [r for r in assignments_for_pass(landing, records) if r["asta_id"] in known]
        )
        unlinked = 0
        for row in assignments:
            entry = bridge.get(row["player_uuid"])
            fid = entry.get("fantacalcio_id") if entry else None
            if known_players is not None and fid not in known_players:
                fid = None
            row["fantacalcio_id"] = fid
            unlinked += fid is None
        if not dry_run:
            from fantabot.db import database_manager
            from fantabot.db.repositories.aste import AsteRepository

            with database_manager.get_session() as session:
                repo = AsteRepository(session)
                repo.upsert_auctions(auctions)
                repo.upsert_events(events)
                repo.upsert_assignments(assignments)
                session.commit()
            checkpoint.write(new_offset)

        if unlinked:
            console.print(f"[yellow]{unlinked} assignment(s) carry no player link[/yellow]")
        _report_dropped(dropped)
        return len(records), max(0, size - new_offset), deferring

    from sqlalchemy.exc import SQLAlchemyError

    while True:
        try:
            carried, behind, deferred = pass_once()
        except LandingZoneMissing as exc:
            # Named, with the command that would create it. Silence here reads
            # as a quiet evening, which is the one thing it must not read as.
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
        except SQLAlchemyError as exc:
            # The checkpoint has not moved, so nothing is lost — the next pass
            # re-reads exactly what this one could not write. Said out loud,
            # because a raw driver traceback tells you the connection failed and
            # not that the collector is fine and the catch-up is pending.
            console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
            console.print("Collection is unaffected — the landing zone keeps growing.")
            console.print("Start it with: [bold]docker compose up -d[/bold], then re-run.")
            if not follow:
                raise typer.Exit(1) from exc
            time.sleep(interval)
            continue

        suffix = " (dry run — nothing written)" if dry_run else ""
        # Work skipped without a word is work nobody counts.
        note = " · ladders deferred" if deferred else ""
        # Lag is reported every pass, not only when it is large: a loader that
        # only speaks up when it is already behind gives no warning it is losing.
        console.print(f"carried {carried} · {behind} bytes behind{note}{suffix}")
        if deferred:
            # A backlog is not a quiet pass, in either mode. One interval per
            # window turned a thirty-six-pass catch-up into six minutes of
            # sleeping; returning here instead turned a one-shot load into a
            # 32 MB one that still exited 0. `--follow` means keep watching
            # after catching up, never "the only mode that catches up".
            continue
        if not follow:
            return
        time.sleep(interval)


def aste_collect(
    out: Path = typer.Option(..., help="Landing-zone JSONL to append to."),
    seed: Path = typer.Option(None, help="Registry to follow in full. Omit to use --one."),
    auction: str = typer.Option("", "--one", help="A single auction uuid to follow."),
    shard: str = typer.Option("", help="Its Firebase shard. Required with --one."),
    pool: int = typer.Option(0, help="Concurrent streams. 0 = the measured default."),
    reload_seed: float = typer.Option(
        60.0,
        help="Seconds between re-reads of --seed, to pick up auctions that open later. 0 = off.",
    ),
) -> None:
    """Subscribe to live auctions and append every state to the landing zone.

    Writes to disk, never to the database. A database outage must not be able to
    stop collection — the file survived eleven process kills on 2026-08-26 and a
    socket would not have.
    """
    import asyncio
    import json

    from fantabot.aste.landing import LandingZone
    from fantabot.aste.registry import AuctionConfig, from_seed_row
    from fantabot.aste.stream import Outcome, SinkFailed, watch_auction
    from fantabot.aste.supervisor import DEFAULT_POOL, Report, Supervisor
    from fantabot.aste.transport import open_stream

    if seed is None and not (auction and shard):
        console.print("[red]Give either --seed, or both --one and --shard.[/red]")
        raise typer.Exit(2)

    def read_seed() -> list[AuctionConfig]:
        return [
            from_seed_row(row, asta_type="mantra")
            for row in json.loads(seed.read_text(encoding="utf-8"))
        ]

    if seed is not None:
        configs = read_seed()
    else:
        configs = [AuctionConfig(auction_id=auction, db_shard=shard, asta_type="mantra")]

    # Only a seed can grow. With --one there is nothing to re-read, and an asta
    # that opens later is an asta the collector never hears about — which is how
    # every room opening after the first scan was lost.
    reload = read_seed if seed is not None and reload_seed > 0 else None

    zone = LandingZone(out)
    limit = pool or DEFAULT_POOL
    console.print(f"following {len(configs)} auction(s) -> {out}")
    if len(configs) > limit:
        # The bound is ours, and on a live evening it is permanent: a watcher
        # does not finish, so a queued auction never gets a permit and never
        # connects at all. Silence here cost 145 of 395 auctions on 2026-08-27.
        console.print(
            f"[red]--pool is {limit}: {len(configs) - limit} auction(s) will wait for a "
            "slot that a live evening never frees. Raise it.[/red]"
        )
    if reload is not None:
        console.print(
            f"re-reading {seed} every {reload_seed:g}s for new auctions — "
            "runs until interrupted"
        )

    async def watch(config: AuctionConfig) -> Outcome:
        return await watch_auction(
            config.auction_id,
            config.db_shard,
            open_stream=open_stream,
            on_state=lambda state: zone.write(config.auction_id, state),
            sleep=asyncio.sleep,
        )

    supervisor = Supervisor(watch=watch, sleep=asyncio.sleep, pool=limit)

    def heartbeat(report: Report) -> None:
        """The only thing that speaks during a run with no end.

        `live / expected` is the number that would have shown 250 of 395
        following, hours before a row count did.
        """
        console.print(f"{report.summary()} · {zone.written} states written")

    try:
        report = asyncio.run(
            supervisor.run(
                configs, reload=reload, reload_every=reload_seed, heartbeat=heartbeat
            )
        )
    except SinkFailed as exc:
        # Not a transport problem, and not survivable by reconnecting: if the
        # sink is failing, continuing would reconnect forever and store nothing.
        console.print(f"[red]the landing zone failed: {exc}[/red]")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print(f"[yellow]stopped — {zone.written} states written[/yellow]")
        return

    console.print(f"{report.summary()} · {zone.written} states written")


def aste_backfill(
    events: Path = typer.Argument(..., help="Collector log: one merged state per line."),
    seed: Path = typer.Option(..., help="The scan seed describing each auction."),
    listone: Path = typer.Option(
        Path("data/aste_live/listone_map.json"),
        help="uuid -> fantacalcio_id bridge from GET /v2/listone.",
    ),
    asta_type: str = typer.Option("mantra", help="Format the seed was collected for."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and report, write nothing."),
) -> None:
    """Load a recorded collector log into `asta`, `asta_event` and `asta_assignment`.

    The same code path the live loader uses. A backfill that grows its own way of
    building rows leaves one of the two untested, and the difference shows up on
    an evening that cannot be collected twice.
    """
    import json

    from fantabot.aste.backfill import build, read_jsonl
    from fantabot.db.models.aste import ASTA_TYPES

    # Checked before any work: asta_type is NOT NULL and only two values exist,
    # so a typo caught here beats a constraint violation after building 144,518
    # rows.
    if asta_type not in ASTA_TYPES:
        console.print(f"[red]{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}")
        raise typer.Exit(2)
    for label, path in (("events", events), ("seed", seed)):
        if not path.exists():
            console.print(f"[red]{label} file not found: {path}[/red]")
            raise typer.Exit(2)

    states = read_jsonl(events)
    seed_rows = json.loads(seed.read_text(encoding="utf-8"))
    bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    if not bridge:
        console.print(f"[yellow]no listone at {listone}; assignments will carry no player link")

    known_players: frozenset[int] | None = None
    if not dry_run:
        from fantabot.db import database_manager
        from fantabot.db.repositories.aste import AsteRepository

        with database_manager.get_session() as session:
            known_players = AsteRepository(session).known_player_ids()

    built = build(states, seed_rows, bridge, asta_type, known_players)
    console.print(
        f"auctions {len(built.auctions)} · events {len(built.events)} from {len(states)} states"
        f" · assignments {len(built.assignments)}"
    )
    _report_dropped(built.dropped_events)
    unlinked = built.unlinked_players
    if unlinked:
        # A staleness signal, not a warning to scroll past: a few is a transfer
        # window, a lot means the reference table no longer describes the listone.
        console.print(
            f"[yellow]{unlinked} assignment(s) carry no player link — "
            "`players` is behind the listone[/yellow]"
        )

    if dry_run:
        console.print("[yellow]dry run — nothing written[/yellow]")
        return

    from fantabot.db import database_manager
    from fantabot.db.repositories.aste import AsteRepository

    with database_manager.get_session() as session:
        repo = AsteRepository(session)
        repo.upsert_auctions(built.auctions)
        repo.upsert_events(built.events)
        repo.upsert_assignments(built.assignments)
        session.commit()
        console.print(f"[green]stored — {repo.count_assignments()} assignments in total")


#: In the order they were declared in `cli.py`, because Typer lists commands in
#: registration order and `--help` should not reshuffle between releases.
COMMANDS = (aste_scan, fantalab_login, aste_load, aste_collect, aste_backfill)


def register(app: typer.Typer) -> None:
    """Attach every harvest command to the root app."""
    for command in COMMANDS:
        app.command()(command)

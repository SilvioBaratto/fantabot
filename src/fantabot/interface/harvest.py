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

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from fantabot.adapters.files.lock import COLLECTOR, LOADER
from fantabot.interface.console import console

if TYPE_CHECKING:  # annotations only
    from fantabot.adapters.persistence.repositories.aste import EventWrite
    from fantabot.domain.harvest.backfill import DroppedEvents



#: What each of the harvest home's files is called. Named here rather than at each option,
#: because a default spelled four times is a default that drifts three ways.
SEED_NAME = "seed.json"
LANDING_NAME = "live.jsonl"
LISTONE_NAME = "listone_map.json"


def _home(name: str) -> Path:
    """`<harvest home>/<name>`, resolved when the command runs, never at import.

    Typer evaluates a default in the `def` line once, when this module is imported — which
    would bind the home of whichever process imported first and defeat the whole point of
    `config.harvest_dir` being a function. So every path option below defaults to `None`
    and is resolved through here in the body instead.
    """
    from fantabot.config import harvest_dir

    return harvest_dir() / name


@contextmanager
def _held(role: str, landing: Path, *, take: bool = True) -> Iterator[None]:
    """Hold a role for `landing`, or report the refusal as the CLI reports refusals.

    Exit code 2, the same as a missing seed: "you asked for something that cannot be
    done", not "it went wrong halfway". `take=False` is the dry-run escape — see the one
    call site that uses it.
    """
    from fantabot.adapters.files.lock import RoleBusy, role_lock

    if not take:
        yield
        return
    try:
        with role_lock(landing, role):
            yield
    except RoleBusy as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None


def aste_scan(
    seed: Path | None = typer.Option(
        None, help="Registry to merge into and rewrite. Default: the harvest home's seed.json."
    ),
    only: str = typer.Option("", help="Keep one format: mantra or classic. Empty = both."),
) -> None:
    """Ask FantaLab which auctions are live and merge them into the registry.

    Replaces walking the page's React tree with one authenticated GET — spike S2.
    Both formats are fetched: filtering is a query, never a decision taken at
    collection time, and the poller filtering to Mantra is what threw away 85%
    of the population.
    """
    import json

    from fantabot.adapters.http.harvest.client import AuthExpired, LiveAuctionsClient, ScanEmpty
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.fantalab_store import FantalabStore
    from fantabot.config import settings
    from fantabot.domain.harvest.registry import from_seed_row, merge, to_seed_rows
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import FantalabSessionMissing

    seed = seed or _home(SEED_NAME)
    cipher = TokenCipher(settings.fantabot_encryption_key)
    # The client is built inside the session, so the bearer is resolved by the adapter
    # and never lands in a local here. `from_store` raises when nothing is stored.
    try:
        with database_manager.get_session() as session:
            client = LiveAuctionsClient.from_store(FantalabStore(session, cipher))
    except FantalabSessionMissing as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None

    try:
        scanned = client.live_auctions()
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
    from fantabot.adapters.browser.capture import read_storage_state, real_browser
    from fantabot.application.fantalab_login import LoginAborted
    from fantabot.application.fantalab_login import run as run_login
    from fantabot.application.login_wait import CaptureUnreadable
    from fantabot.domain.tokens.errors import SignInWindowClosed

    try:
        run_login(
            force=force,
            browser_factory=lambda: real_browser(browser or None),
            read_state=read_storage_state,
            report=console,
        )
    except LoginAborted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(exc.code) from None
    except (SignInWindowClosed, CaptureUnreadable) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None


def _constraint_of(exc: Exception) -> str:
    """The constraint a driver says was violated, or the first line of what it did say.

    psycopg2 carries it on ``exc.orig.diag.constraint_name``; another driver may carry
    nothing. The fallback is the driver's own first line rather than the exception type,
    because "IntegrityError" is exactly the message that sent an operator looking at the
    network on 2026-09-05.
    """
    orig = getattr(exc, "orig", None)
    name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if name:
        return str(name)
    text = str(orig if orig is not None else exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


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


def _report_written(written: EventWrite) -> None:
    """Say what reached the table, and shout when rows were thrown away.

    Only the discard is loud. A window that is entirely `already_present` is the
    normal shape of a re-read and warning about it every pass would train the
    operator to ignore the line that matters.

    The line that matters is `unknown auction`: those events named an auction with
    no `asta` row, so they were dropped with nowhere to go and the byte offset moved
    on regardless. Reporting a full write while discarding every row is how 2.1
    million Classic events stayed missing for a week.
    """
    if written.discarded:
        console.print(
            f"[red]{written.unknown_auction} event(s) DISCARDED — no auction row to "
            f"attach them to. Re-scan the seed before this window is passed.[/red]"
        )
    elif written.inserted:
        console.print(f"[dim]events: {written.summary()}[/dim]")


def aste_load(
    landing: Path | None = typer.Argument(
        None, help="Landing-zone JSONL the collector appends to. Default: the harvest home's."
    ),
    seed: Path | None = typer.Option(
        None, help="The scan seed describing each auction. Default: the harvest home's."
    ),
    listone: Path | None = typer.Option(
        None,
        help="uuid -> fantacalcio_id bridge from GET /v2/listone. Default: the harvest home's.",
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

    from fantabot.adapters.files.stopflag import (
        clear_stop,
        clear_unless_precleared,
        read_stop,
        stop_path,
    )

    # Aliased: the `listone` *parameter* (a Path) already holds this name in this function's
    # scope, and `entries_only` is what makes this reader tolerant of the versioned envelope
    # (`version`, `count`, `season`, `fetched_at`) a cache file now carries alongside its
    # entries — see `listone.entries_only`'s own docstring for why a structural filter beats
    # naming the four keys here.
    from fantabot.adapters.http.fantalab import listone as listone_module
    from fantabot.adapters.persistence.models.aste import ASTA_TYPES
    from fantabot.application.harvest_loader import (
        DEFAULT_WINDOW_BYTES,
        CachedPlayerIds,
        Checkpoint,
        FoldCheckpoint,
        LandingZoneMissing,
        SeedRows,
        assignment_rows,
        catching_up,
        read_from,
    )
    from fantabot.domain.harvest import incremental as incremental
    from fantabot.domain.harvest.backfill import auction_rows, event_rows

    landing = landing or _home(LANDING_NAME)
    seed = seed or _home(SEED_NAME)
    listone = listone or _home(LISTONE_NAME)
    if asta_type not in ASTA_TYPES:
        console.print(f"[red]{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}")
        raise typer.Exit(2)
    if not seed.exists():
        console.print(f"[red]seed file not found: {seed}[/red]")
        raise typer.Exit(2)

    raw_bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    bridge = listone_module.entries_only(raw_bridge) if isinstance(raw_bridge, dict) else {}
    # One pass holds its window several times over. Uncapped, the cost of a pass
    # grows with how far behind the loader is — so the further it falls, the less
    # able it is to start, and a 1.14 GB backlog could not be loaded at all.
    pass_window = window or DEFAULT_WINDOW_BYTES
    checkpoint = Checkpoint(landing)
    fold_checkpoint = FoldCheckpoint(landing)

    # The fold's position in the same file the byte offset describes. `None` from a
    # missing, truncated or older-version state file means re-fold from the
    # beginning, so the offset is reset with it — the two must describe the same
    # position or a ladder is rebuilt from nothing, or has its rungs appended twice.
    fold_state = fold_checkpoint.read()
    if fold_state is None and checkpoint.read():
        console.print(
            "[yellow]no usable reducer state — re-reading the landing zone from the "
            "start so the ladders are rebuilt whole[/yellow]"
        )
        checkpoint.write(0)
    fold = fold_state if fold_state is not None else incremental.empty()
    # Re-read every pass, not once: the collector adopts auctions that open
    # after it started, and a loader holding the startup seed calls their events
    # unknown and advances its checkpoint past them.
    seed_source = SeedRows(seed)
    # `players`, by contrast, only moves when the quotazioni scraper runs, and
    # reading it is a session and 1,492 ids across the wire.

    def fetch_known_players() -> frozenset[int]:
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories.aste import AsteRepository

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
        """Records carried, bytes still behind the writer, and whether another pass
        is coming — the only thing the loop below asks of the third value.

        (It used to mean "the ladder rebuild was deferred". Nothing is deferred now:
        the fold runs every pass, because the byte offset advances every pass and a
        skipped fold would never see those records again.)
        """
        nonlocal fold
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
        # Still the answer to "is another pass coming", which is all the loop below
        # asks of it. It no longer gates the assignment work: that deferral existed
        # because rebuilding assignments meant re-reading the whole landing zone —
        # 9.4 s and ~880 MB, thirty-four times over on the 2026-08-28 backlog. The
        # incremental fold costs O(window), so there is nothing left to defer, and
        # deferring it now would be worse than pointless: the byte offset advances
        # on every pass, so records skipped here would never be folded at all.
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

        # Fold every pass, unconditionally. `advance` returns a NEW state, which is
        # bound below only after the write commits: a failed write leaves the byte
        # offset where it was, so the same window is re-read, and folding it onto a
        # state that already holds it appends the same rungs again — a ladder that
        # steps downwards, which is the corruption an opponent model reads as a
        # bidding war.
        next_fold, closed = incremental.advance(fold, records)

        # `drain` before writing, always. The node keeps returning a closed state
        # until the next call begins, so one window routinely carries the same sale
        # twice — and a single INSERT whose VALUES repeat a conflict key raises
        # "ON CONFLICT DO UPDATE command cannot affect row a second time", which the
        # handler below would report as a database outage and retry for ever.
        assignments = [
            r for r in assignment_rows(incremental.drain(closed)) if r["asta_id"] in known
        ]
        unlinked = 0
        for row in assignments:
            entry = bridge.get(row["player_uuid"])
            fid = entry.get("fantacalcio_id") if entry else None
            if known_players is not None and fid not in known_players:
                fid = None
            row["fantacalcio_id"] = fid
            unlinked += fid is None
        if not dry_run:
            from fantabot.adapters.persistence import database_manager
            from fantabot.adapters.persistence.repositories.aste import AsteRepository

            with database_manager.get_session() as session:
                repo = AsteRepository(session)
                repo.upsert_auctions(auctions)
                written = repo.upsert_events(events)
                repo.upsert_assignments(assignments)
                session.commit()
            # Order matters: the write commits, then the two checkpoints move
            # together, then the state is bound. Anything else lets the fold and the
            # offset describe different positions in the same file.
            checkpoint.write(new_offset)
            fold_checkpoint.write(next_fold)
            fold = next_fold

        if unlinked:
            console.print(f"[yellow]{unlinked} assignment(s) carry no player link[/yellow]")
        _report_dropped(dropped)
        if not dry_run:
            _report_written(written)
        return len(records), max(0, size - new_offset), deferring

    from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

    # The loader's role lock, for the run's whole life. A dry run deliberately takes
    # nothing: its checkpoint never moves, so two of them cannot disagree, and refusing a
    # read-only inspection while a real loader follows would be a lock the operator has to
    # work around rather than one that protects anything.
    with _held(LOADER, landing, take=not dry_run):
        # The loader's half of the cooperative stop. `POST /harvest/load` registers a stop
        # for this command, so the app draws a Stop button for it; until now that button
        # wrote a flag into a file nobody read, and on Windows — where `ProcessJob.stop`
        # sends no signal at all — the first click did nothing whatsoever.
        #
        # The clear is the load-bearing half. Nothing ever removed the loader's flag, so
        # it latched: `<landing>.loader.stop` kept `{"state": "exit"}` for ever, and every
        # later run's *first* click read that stale exit, skipped the disarm stage and went
        # straight to the 15 s grace and SIGKILL. That is precisely the latching failure
        # `stopflag` exists to prevent — its contract was written as universal and only the
        # collector implemented it. Clearing here is safe because the role lock above is
        # ours, so nothing else can be waiting on what this erases.
        stop_file = stop_path(landing, LOADER)
        clear_unless_precleared(stop_file)
        try:
            while True:
                try:
                    carried, behind, deferred = pass_once()
                except LandingZoneMissing as exc:
                    # Named, with the command that would create it. Silence here reads
                    # as a quiet evening, which is the one thing it must not read as.
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(2) from None
                except IntegrityError as exc:
                    # Ours, not theirs, and the one case retrying cannot fix: the checkpoint
                    # has not moved, so the next pass re-reads the same window and violates
                    # the same constraint. Reported as an outage until 2026-09-05, when a
                    # `UniqueViolation` on `asta.key` — a stranded identity sequence — was
                    # retried behind "database unreachable" for as long as anyone watched.
                    # So it exits non-zero in **both** modes: `--follow` waits for something
                    # that changes, and this does not.
                    console.print(f"[red]constraint violated: {_constraint_of(exc)}[/red]")
                    console.print("The next pass reads the same window and violates it again.")
                    console.print("Collection is unaffected — the landing zone keeps growing.")
                    raise typer.Exit(1) from exc
                except OperationalError as exc:
                    # The outage, and the only branch that retries. The checkpoint has not
                    # moved, so nothing is lost — the next pass re-reads exactly what this one
                    # could not write. Said out loud, because a raw driver traceback tells you
                    # the connection failed and not that the collector is fine and the
                    # catch-up is pending.
                    console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
                    console.print("Collection is unaffected — the landing zone keeps growing.")
                    console.print("Start it with: [bold]fantabot-app db start[/bold], then re-run.")
                    if not follow:
                        raise typer.Exit(1) from exc
                    time.sleep(interval)
                    # Checked here too, and this is the branch that most needs it: it is
                    # the only one that loops for ever by design. Its `continue` skips the
                    # check at the bottom of the loop, so without this the Stop button is
                    # dead in exactly the state an operator most wants it — database down,
                    # loader retrying on a timer — and on Windows, where no signal is sent,
                    # the only reachable stop was the 15 s grace and SIGKILL.
                    if (stage := read_stop(stop_file)) is not None:
                        console.print(f"[yellow]stopped ({stage})[/yellow]")
                        return
                    continue
                except SQLAlchemyError as exc:
                    # Neither of the above, and deliberately not folded into either: calling
                    # everything an outage is the defect the two branches above exist to undo,
                    # and guessing again here would rebuild it one type further out.
                    console.print(f"[red]the database refused the write: {type(exc).__name__}[/red]")
                    console.print("Collection is unaffected — the landing zone keeps growing.")
                    raise typer.Exit(1) from exc

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
                # Read between passes, never inside one. A pass is a read-and-commit, and
                # a stop that interrupted it would ask the loader to abandon a window it
                # has already carried — the checkpoint's whole contract is that it moves
                # only after the commit.
                #
                # Both stages wind the loader down. It has nothing to disarm, exactly like
                # the collector: the flag says which stage was asked, and what a stage
                # *means* is the command's to decide.
                if (stage := read_stop(stop_file)) is not None:
                    console.print(f"[yellow]stopped ({stage})[/yellow]")
                    return
        finally:
            # Ours to clear however the run ended, including the error exits: a `.stop`
            # left in the harvest home makes `ls` there lie about what is running.
            clear_stop(stop_file)


def aste_collect(
    out: Path | None = typer.Option(
        None, help="Landing-zone JSONL to append to. Default: the harvest home's live.jsonl."
    ),
    seed: Path | None = typer.Option(
        None, help="Registry to follow in full. Omit with --one. Default: the harvest home's."
    ),
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

    from fantabot.adapters.files.landing import LandingZone
    from fantabot.adapters.files.stopflag import (
        clear_stop,
        clear_unless_precleared,
        stop_path,
        wait_for_stop,
    )
    from fantabot.adapters.http.harvest.stream import Outcome, SinkFailed, watch_auction
    from fantabot.adapters.http.harvest.transport import open_stream
    from fantabot.application.harvest_supervisor import DEFAULT_POOL, Report, Supervisor
    from fantabot.domain.harvest.registry import AuctionConfig, from_seed_row

    out = out or _home(LANDING_NAME)
    # The home's seed is the default only when no single auction was named: `--seed` being
    # unset is how this command is *told* there is one auction to follow, so a default that
    # filled it in unconditionally would turn every `--one` run into a seed run.
    if seed is None and not auction:
        seed = _home(SEED_NAME)
    if seed is None and not (auction and shard):
        console.print("[red]Give either --seed, or both --one and --shard.[/red]")
        raise typer.Exit(2)
    if seed is not None and not seed.exists():
        # Reported, not raised. With `--seed` required this was always a path the operator
        # had typed; defaulted, the everyday no-argument run reaches it, and a traceback
        # out of `read_seed` is a poor way to say "run `harvest scan` first".
        console.print(f"[red]seed file not found: {seed}[/red]")
        raise typer.Exit(2)

    # Only a seed can grow. With --one there is nothing to re-read, and an asta
    # that opens later is an asta the collector never hears about — which is how
    # every room opening after the first scan was lost.
    if seed is None:
        configs = [AuctionConfig(auction_id=auction, db_shard=shard, asta_type="mantra")]
        reload: Callable[[], list[AuctionConfig]] | None = None
    else:
        # Rebound because the closure outlives the narrowing: `seed` is `Path | None` in
        # this scope, and a re-read hours into an evening must not be able to see `None`.
        registry = seed

        def read_seed() -> list[AuctionConfig]:
            return [
                from_seed_row(row, asta_type="mantra")
                for row in json.loads(registry.read_text(encoding="utf-8"))
            ]

        configs = read_seed()
        reload = read_seed if reload_seed > 0 else None

    # The collector's role lock, held for the run's whole life. Two collectors against one
    # landing zone write every state twice, and the duplicate is indistinguishable from a
    # real re-observation once it is on disk.
    with _held(COLLECTOR, out):
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

        # The cooperative stop, and it is not an alternative to Ctrl-C — it is the only
        # one a supervisor has. `CTRL_BREAK_EVENT`, the sole signal that reaches a child
        # process group on Windows, arrives as SIGBREAK and terminates the interpreter
        # before `except KeyboardInterrupt` below can run, so a supervised collector there
        # never ran its own shutdown at all. This is polled, so the shutdown is always
        # ours. See `adapters/files/stopflag`.
        #
        # The collector has nothing to disarm, so it honours *both* stages as "wind down"
        # — the same way its first Ctrl-C already ends it while `asta bid`'s first one
        # only disarms. The flag carries which stage was asked; what a stage means is the
        # command's to decide.
        #
        # Cleared before the wait starts, and that is the whole staleness story: a run
        # that died at *exit* left the flag on disk, and reading it here would quit at
        # startup for a reason that expired. We are inside `_held`, so the role lock is
        # ours and nothing else can be waiting on what this erases.
        flag = stop_path(out, COLLECTOR)
        clear_unless_precleared(flag)

        async def stop() -> str:
            return await wait_for_stop(flag, sleep=asyncio.sleep)

        try:
            report = asyncio.run(
                supervisor.run(
                    configs,
                    reload=reload,
                    reload_every=reload_seed,
                    heartbeat=heartbeat,
                    stop=stop,
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
        finally:
            # Ours to clear, whichever way the run ended. The flag is addressed to this
            # pid so a leftover cannot latch onto the next run, but leaving `.stop` files
            # in the harvest home would make `ls` there lie about what is happening.
            clear_stop(flag)

        if report.stopped is not None:
            console.print(
                f"[yellow]stopped ({report.stopped}) — {zone.written} states written[/yellow]"
            )
            return

        console.print(f"{report.summary()} · {zone.written} states written")


def aste_backfill(
    events: Path = typer.Argument(..., help="Collector log: one merged state per line."),
    seed: Path | None = typer.Option(
        None, help="The scan seed describing each auction. Default: the harvest home's."
    ),
    listone: Path | None = typer.Option(
        None,
        help="uuid -> fantacalcio_id bridge from GET /v2/listone. Default: the harvest home's.",
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

    # Aliased for the same reason `aste_load` aliases it: the `listone` parameter already
    # holds this name here, and `entries_only` tolerates the versioned envelope a cache file
    # now carries.
    from fantabot.adapters.http.fantalab import listone as listone_module
    from fantabot.adapters.persistence.models.aste import ASTA_TYPES
    from fantabot.domain.harvest.backfill import build, read_jsonl

    seed = seed or _home(SEED_NAME)
    listone = listone or _home(LISTONE_NAME)
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
    raw_bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    bridge = listone_module.entries_only(raw_bridge) if isinstance(raw_bridge, dict) else {}
    if not bridge:
        console.print(f"[yellow]no listone at {listone}; assignments will carry no player link")

    known_players: frozenset[int] | None = None
    if not dry_run:
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories.aste import AsteRepository

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

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.aste import AsteRepository

    with database_manager.get_session() as session:
        repo = AsteRepository(session)
        repo.upsert_auctions(built.auctions)
        repo.upsert_events(built.events)
        repo.upsert_assignments(built.assignments)
        session.commit()
        console.print(f"[green]stored — {repo.count_assignments()} assignments in total")


#: `(name, function)`, in declaration order — Typer lists commands in registration
#: order and `--help` should not reshuffle between releases.
#:
#: The names are given explicitly rather than derived from the function names,
#: because the group supplies the prefix now: `harvest scan`, not `harvest aste-scan`.
#: `fantalab_login` is not a harvest command at all and moves to the `auth` group; it
#: lived here only because this is where the FantaLab session store was first needed.
HARVEST_COMMANDS: tuple[tuple[str, Callable[..., None]], ...] = (
    ("scan", aste_scan),
    ("load", aste_load),
    ("collect", aste_collect),
    ("backfill", aste_backfill),
)

#: The name *within* the `auth` group, not the path to it. It read
#: `"auth fantalab-login"` and was registered as that literal, so the only way to
#: invoke it was `fantabot auth "auth fantalab-login"` and the obvious spelling
#: answered `No such command 'fantalab-login'`.
AUTH_COMMANDS: tuple[tuple[str, Callable[..., None]], ...] = (
    ("fantalab-login", fantalab_login),
)


def register(harvest: typer.Typer, auth: typer.Typer) -> None:
    """Attach this module's commands to the groups they belong to."""
    for name, command in HARVEST_COMMANDS:
        harvest.command(name)(command)
    for name, command in AUTH_COMMANDS:
        auth.command(name)(command)

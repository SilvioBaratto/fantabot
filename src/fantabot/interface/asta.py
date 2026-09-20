"""The offline asta commands: `asta optimize` and `asta legality`. Read-only, no FantaLab.

The thin I/O shell: fetch the Mantra pool, values and prices from Postgres, hand them to the
pure engine (legality / value / optimizer / report), and print. Registered on the root app
by ``register(app)``, mirroring ``aste/cli.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol, cast

import typer

if TYPE_CHECKING:
    from rich.console import RenderableType

    from fantabot.domain.asta.roles import MantraPlayer
    from fantabot.domain.shared.values import SentimentRow

from pathlib import Path

from fantabot.application.asta_planner import read_plan_inputs
from fantabot.application.plan_request import DEFAULT_NUM_CREDITS, DEFAULT_NUM_TEAMS
from fantabot.domain.asta.legality import build_legality, fieldable_schemi, load_compat
from fantabot.domain.asta.live import normalize
from fantabot.domain.asta.opponents import format_advisory, format_opponents
from fantabot.domain.asta.optimizer import InfeasibleRoster
from fantabot.domain.asta.report import (
    build_pool,
    format_legality,
    format_roster,
    parse_ids,
    parse_replay_lines,
)
from fantabot.domain.asta.sentiment import SentimentWeights
from fantabot.domain.asta.state import AstaState, RosterRules, rules_for_room
from fantabot.domain.classic.state import ClassicRosterRules
from fantabot.interface.console import console
from fantabot.interface.options import (
    SEASON,
    BargainBeta,
    BargainShare,
    CeilingAlpha,
    CorpusCredits,
    CorpusTeams,
    Lega,
    Season,
    Sentiment,
    SentimentRun,
    TiltK,
)


class _SentimentSource(Protocol):
    """What the CLI asks of the sentiment feed. A Protocol so tests need no database."""

    def all_latest(
        self, *, data_run: date | None = ...
    ) -> dict[str, SentimentRow]: ...


def _today() -> date:
    """The one wall-clock read in the asta path. The golden harness patches exactly this.

    `sentiment.py` takes `as_of` as a parameter and never reads a clock, on purpose. This
    is the shell that supplies it — and it is a named function rather than three inline
    `date.today()` calls because the harness has to freeze it. `sentiment.py:153` decays
    confidence on a 7-day half-life, and every row shares one `data_run`, so one day of
    drift rescales every reading: the same inputs printed `obj 2273.1` today, `2209.1`
    tomorrow, `1936.5` in a week, with roster membership changing too. Three reads would
    be three things to freeze in lockstep, and `tests/test_asta_clock.py` keeps it at one.
    """
    return date.today()


def parse_run_date(run: str) -> date | None:
    """``--sentiment-run`` as a date; empty means "each player's newest". Pure."""
    if not run:
        return None
    try:
        return date.fromisoformat(run)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--sentiment-run must be YYYY-MM-DD, got {run!r}"
        ) from exc


def sentiment_rows(
    source: _SentimentSource, *, enabled: bool, run: str
) -> dict[str, SentimentRow] | None:
    """Fetch the readings, or ``None`` when the operator asked for the ablation.

    Two lines of translation over `application/plan_request.resolve_sentiment`: the *rule*
    — that an empty result is refused rather than passed through, because valuing on "no
    rows" is numerically identical to ``--no-sentiment`` and means something entirely
    different — belongs beside the plan it governs, and the app needs it too. What stays
    here is turning that refusal into the exception a Typer body may raise, and parsing
    the run string, which raises the same kind.
    """
    from fantabot.application.plan_request import NoSentimentRows, resolve_sentiment

    try:
        rows = resolve_sentiment(source, enabled=enabled, run=parse_run_date(run))
    except NoSentimentRows as exc:
        raise typer.BadParameter(str(exc)) from exc
    return None if rows is None else dict(rows)


def bid_writer(
    *,
    auto_act: bool,
    arm: bool,
    send: Callable[[dict[str, Any]], Any],
    node: str = "auction",
) -> Callable[[dict[str, Any]], Any]:
    """``send`` if both locks are open, otherwise a write that goes nowhere.

    **Why two.** ``FANTABOT_AUTO_ACT`` is read inside ``place_raise`` at call time and comes
    from ``.env``, so flipping it arms every invocation at once — for the rest of that
    process and every process after it. The operator who edits ``.env`` in the morning is
    not necessarily the one running the command at 21:47.

    ``--arm`` is therefore *positive* and defaults off. The asymmetry decides the direction:
    forgetting an opt-in flag means watching, forgetting an opt-out flag means spending money.

    The disarmed writer returns a real ``BidOutcome`` rather than ``None``. The loop reads
    ``.sent`` through ``getattr``, so ``None`` would be accidentally right and would stop
    being right the moment anything else is read off it.
    """
    from fantabot.adapters.http.fantalab.rtdb import BidOutcome

    if auto_act and arm:
        return send

    def hold(payload: dict[str, Any]) -> BidOutcome:
        price = payload.get("price")
        # The same coercion `place_raise` applies: `True` is an `int` in Python and a
        # price of `True` is not a price.
        clean = price if isinstance(price, int) and not isinstance(price, bool) else 0
        return BidOutcome(price=clean, node=node, dry_run=True, sent=False, status=None)

    return hold


@contextmanager
def _disarm_on_sigint(armed: list[bool]) -> Iterator[None]:
    """First Ctrl-C clears `armed[0]` and keeps going; the second exits. One handler, two commands.

    Mid-auction the operator far more often wants "stop bidding, keep showing me the room"
    than "quit" — `news fetch`'s pattern, for the same reason. The writer must read
    `armed[0]` on **every** bid for this to mean anything: a writer bound once at loop start
    keeps bidding after the operator disarmed.

    This lived inline in `asta_room`, and `asta_bid` never had it at all — two copies of the
    live loop drifted, and the one that lost the disarm was the one that spends credits.
    Lifted here so there is one. A run that was never armed has nothing to disarm, so its
    first Ctrl-C exits.

    **Restored in a `finally`.** `asta_room` restored on the line after its loop, so a loop
    that raised left the handler installed for the rest of the process.

    Off the main thread Python refuses a signal handler. That costs the graceful disarm and
    nothing else; refusing to run the room over it would be the worse trade.

    ⚠ **It counts its own interrupts rather than reading `armed[0]`, and that is the sibling
    of the defect `stop_poll` carries a note about.** Two mechanisms clear one list, and on
    POSIX `ProcessJob.stop` writes the flag *before* it signals — so if the loop's own poll
    honours that flag in the window between the two, `armed[0]` is already `False` when the
    `SIGINT` lands and the handler reads one click as two. An armed run ends on stage one.
    The keyboard contract is unchanged, which is the point: once disarms, twice exits, and
    `armed_at_start` keeps "a run that was never armed exits on the first".
    """
    import signal

    previous = signal.getsignal(signal.SIGINT)
    armed_at_start = armed[0]
    interrupts = 0

    def _disarm(_signum: int, _frame: Any) -> None:
        nonlocal interrupts
        interrupts += 1
        if not armed_at_start or interrupts > 1:
            signal.signal(signal.SIGINT, previous)
            raise KeyboardInterrupt
        armed[0] = False
        console.print("[yellow]disarmed — still watching. Ctrl-C again to exit.[/yellow]")

    try:
        signal.signal(signal.SIGINT, _disarm)
    except ValueError:
        installed = False
    else:
        installed = True
    try:
        yield
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous)


def _lega_rules(
    lega: int,
    fmt: str,
    *,
    session: Callable[[], Any],
    warn: Callable[[str], None],
    size: int = 0,
) -> tuple[Any, str, str]:
    """`(rules, provenance, format)` — the lega's band, or the built-in one, said out loud.

    Before 2.1 both commands built a bare `RosterRules()` (size 30) whatever the lega
    declared, while the page read the snapshot. On 2026-09-02 that lega declared **25/32**,
    so the command produced a plan it could not buy — `asta optimize` exits with "cannot
    complete the roster: 19/30 filled".

    **The format is detected, never configured** (2.2) — `role_groups` decides it, exactly as
    the lineup path decides it from `sroles`. That path settled the argument first: a per-lega
    flag the operator has to remember is a footgun on a cron path. `--format` survives as an
    override and says so out loud when it disagrees with the lega, because planning a lega as
    something it is not is a thing to do deliberately or not at all.
    The provenance is printed rather than swallowed: a band nobody declared and a band the
    lega stated are different facts.

    **`session` is a factory, not a session**, so no database is opened when there is no
    lega to read. That is not an optimisation: the golden harness serves a sentinel object
    in place of a session, and a command that opened one unconditionally would either blow
    up there or — worse, on a real machine — make the pinned output depend on the database.
    """
    from fantabot.application.lega_reads import rules_for_league
    from fantabot.config import settings
    from fantabot.domain.asta.state import ASSUMED_NOTHING, OPERATOR_DECLARED, resize_band

    resolved = lega or settings.fantabot_league_id
    # **A stated size with no lega named is answered without opening the database.** Not an
    # optimisation: the app's bid route passes no `--lega` — a FantaLab room is not a lega —
    # so `resolved` falls back to `settings.fantabot_league_id`, and reading *that* lega's
    # band only to overwrite its total would leave the child planning with another league's
    # role floors. The size the room declared is the whole answer; the format is
    # `--format`'s, which the route also sends.
    #
    # Keyed on `lega`, the parameter, and not on `resolved`: an operator who names a lega
    # *and* a size meant both, and the floors below are the named lega's. An earlier version
    # read `fmt` here, which on `asta bid` is always set — so the condition collapsed to
    # "any stated size skips the read" and a terminal `--lega X --size N` silently discarded
    # X's floors.
    if size and not lega:
        chosen = fmt or "mantra"
        base = ClassicRosterRules() if chosen == "classic" else RosterRules()
        return resize_band(base, size), OPERATOR_DECLARED, chosen
    if not resolved:
        # No lega to detect from. `mantra` is the standing default and the one the goldens
        # pin; an explicit `--format classic` still wins.
        chosen = fmt or "mantra"
        rules = ClassicRosterRules() if chosen == "classic" else RosterRules()
        return rules, ASSUMED_NOTHING, chosen

    rules, provenance, detected = rules_for_league(session(), resolved)
    if fmt and fmt != detected:
        warn(
            f"lega {resolved} is {detected}; planning {fmt} because --format says so"
        )
        rules = ClassicRosterRules() if fmt == "classic" else RosterRules()
        # The override discards the *lega's* band — it describes a different game and cannot
        # be planned on. It does not discard the operator's own `--size`, which is a separate
        # statement about this room, and returning before applying it left
        # `--lega X --size 25 --format <mismatch>` planning and capping on 30. Silently: the
        # warning above is about the format and says nothing about a size being dropped.
        if size:
            return resize_band(rules, size), OPERATOR_DECLARED, fmt
        return rules, ASSUMED_NOTHING, fmt
    if size:
        # The lega's own band, resized to what the operator states. The *floors* stay the
        # lega's — `resize_band`'s note is why they cannot be re-derived from the total.
        return resize_band(rules, size), OPERATOR_DECLARED, detected
    return rules, provenance, detected


def _callable_ids(
    warn: Callable[[str], None],
    *,
    _fetch: Callable[[], Mapping[str, int]] | None = None,
) -> set[str] | None:
    """`application/plan_request.callable_ids`, in the shape this module's callers hold.

    The narrowing and its fail-open rule moved to `application/` because the app needs
    both. What is left here is the type the room and the bidder already pass around — a
    mutable `set[str] | None`, not a frozenset — and the `_fetch` seam their tests use.
    """
    from fantabot.application.plan_request import callable_ids

    ids = callable_ids(warn=warn, fetch=_fetch)
    return None if ids is None else set(ids)


def _declared_format(
    fantaleague_id: str,
    *,
    warn: Callable[[str], None] = lambda _m: None,
    _fetch: Callable[[str], Any] | None = None,
) -> str | None:
    """A room's own `asta_type`, or `None` if it could not be asked. **Never fatal.**

    `asta live --league` reads the `purchases/<fl>` ledger over the *unauthenticated* RTDB
    — `docs/fantalab/06` says so and the command's own docstring repeats it. This probe is
    a different call: `POST /fantaleague/fetch`, which 401s without a bearer. So every way
    it can fail degrades to `None` and the caller falls through to the recorded corpus and
    then to `--format`. A probe that hard-failed would turn a tokenless command into one
    that needs a login, which is a regression dressed as a fix.

    **The cipher is built inside the guard, not outside it.** `TokenCipher.__init__` raises
    `KeyMissing` before any fetch happens; `asta room` builds its cipher outside a try and
    copying that shape here would crash a machine that simply has no encryption key.

    `AttributeError` is deliberately **not** caught. It is what a fake session raises in a
    test, and swallowing it would make "the probe degraded" indistinguishable from "the
    test wired it wrong" — which is why the seam is `_fetch` rather than a patched session.
    """
    import httpx

    from fantabot.domain.tokens.errors import TokenError

    try:
        if _fetch is None:  # pragma: no cover - the real path needs a session and a bearer
            from fantabot.adapters.http.fantalab import rest
            from fantabot.adapters.persistence import database_manager
            from fantabot.adapters.tokens.fantalab_store import FantalabStore
            from fantabot.config import settings
            from fantabot.domain.tokens.crypto import TokenCipher

            with database_manager.get_session() as session:
                store = FantalabStore(session, TokenCipher(settings.fantabot_encryption_key))
                _fetch = rest.fetcher_from(store)
        room = _fetch(fantaleague_id)
    except (TokenError, httpx.HTTPError, ValueError) as exc:
        # Said out loud rather than swallowed: "no FantaLab session" and "FantaLab is
        # unreachable" send an operator to different fixes, and the next rung down answers
        # for a *different* room population, so which rung failed is worth a line.
        warn(f"the room could not be asked its format ({str(exc) or type(exc).__name__})")
        return None
    asta_type: str | None = getattr(room, "asta_type", None)
    return asta_type


def _recorded_format(fantaleague_id: str) -> str | None:
    """What the harvest corpus holds for this room id, or `None`. The second rung.

    A weaker fact than the room's own word and it is printed as such: it answers "a room
    with this id was harvested, and it was this then". Measured 2026-09-20 the corpus holds
    4,866 rooms — and **not** the operator's own league, which no public scan collects — so
    this rung usually misses on the very room `--league` names. It is the fallback for the
    case where the authenticated probe above it could not run at all.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.aste import AsteRepository

    with database_manager.get_session() as session:
        return AsteRepository(session).asta_type_of(fantaleague_id)


def _report_stopped(report: Any) -> None:
    """The exit summary both live commands print. One place, because they must not diverge.

    `errors` gets its own red line. `400 cycles, 0 bids` reads as an evening in which nothing
    we wanted came up, and that is indistinguishable — in that one line — from a link that was
    down the whole time.
    """
    console.print(
        f"[dim]stopped: {report.cycles} cycles, {report.bids_sent} bids, "
        f"refused {report.refused}[/dim]"
    )
    if report.errors:
        total = sum(report.errors.values())
        console.print(f"[red]{total} cycle(s) failed on the link: {report.errors}[/red]")


def asta_optimize(
    owned: str = typer.Option("", help="Player ids already owned, comma/space separated."),
    budget: float = typer.Option(500.0, help="Remaining credits to spend."),
    lam: float = typer.Option(0.0, "--lam", help="Risk aversion; higher diversifies across clubs."),
    fallbacks: int = typer.Option(3, help="How many next-best plans to show."),
    callable_only: bool = typer.Option(
        True,
        "--callable/--no-callable",
        help="Plan only over players FantaLab's listone can call. Cached; falls back open.",
    ),
    season: Season = SEASON,
    fmt: str = typer.Option(
        "", "--format",
        help="Override the format. Detected from the lega by default: mantra (schemi) or "
        "classic (P/D/C/A). Only pass this to plan a lega as something it is not.",
    ),
    lega: Lega = 0,
    teams: CorpusTeams = DEFAULT_NUM_TEAMS,
    credits: CorpusCredits = DEFAULT_NUM_CREDITS,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Print the current optimal roster and next-best plans. Read-only.

    `--format mantra` (default) plans a 30-man Mantra rosa against the 11 schemi; `--format
    classic` plans the four-band P/D/C/A rosa (25-man `[3,8,8,6]`) against no schema. The pool is
    narrowed to players FantaLab's listone can call, exactly as `asta bid` narrows it — a player
    who can never come up for auction is a slot the evening cannot fill. `--no-callable` restores
    the wider plan.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.plan_request import (
        EmptyPool,
        NoSentimentRows,
        PlanRequest,
        build_plan,
    )
    from fantabot.domain.asta.prices import NoCorpus

    if fmt and fmt not in ("mantra", "classic"):
        raise typer.BadParameter("--format must be 'mantra' or 'classic'")

    ids = (
        _callable_ids(lambda note: console.print(f"[yellow]{note}[/yellow]"))
        if callable_only
        else None
    )

    # Everything below the request is presentation and exit codes. What a plan is built
    # from is `application/plan_request.py`'s to say, and it says it once — the endpoint
    # builds the same value, which is the whole of 1.5.
    from contextlib import ExitStack

    with ExitStack() as stack:
        rules, provenance, fmt = _lega_rules(
            lega,
            fmt,
            session=lambda: stack.enter_context(database_manager.get_session()),
            warn=lambda note: console.print(f"[yellow]{note}[/yellow]"),
        )
    # Said every run, including when nothing was declared — the silence about the assumption
    # is what let a 30-man plan be built for a 25-man lega for a fortnight.
    console.print(f"[dim]roster band: {getattr(rules, 'size', '?')} players ({provenance})[/dim]")

    request = PlanRequest(
        season=season,
        listone=fmt,
        as_of=_today(),
        budget=budget,
        rules=rules,
        owned=frozenset(parse_ids(owned)),
        lam=lam,
        n_fallbacks=fallbacks,
        tilt_k=tilt_k,
        sentiment=sentiment,
        sentiment_run=parse_run_date(sentiment_run),
        callable_ids=None if ids is None else frozenset(ids),
        # Stated, not inherited. This was the sixth of the six call sites and the only one
        # that could not be told a shape: it took `PlanRequest`'s 8x500 default, which is
        # explicit in the code and unreachable to the operator — the "numero scritto nel
        # codice" `docs/fantalab/00 §13` calls a bug, and this task's own premise.
        num_teams=teams,
        num_credits=credits,
    )

    try:
        with database_manager.get_session() as session:
            planned = build_plan(session, request)
    # Three failures, three exit paths, and each says a different thing. `EmptyPool` is a
    # wrong `--format` or an un-scraped season; `InfeasibleRoster` is a rosa that cannot
    # be built at all; `NoSentimentRows` is a mistyped date that would otherwise plan on
    # plain `fvm` without saying so.
    except NoSentimentRows as exc:
        raise typer.BadParameter(str(exc)) from exc
    except (EmptyPool, InfeasibleRoster, NoCorpus) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    result, world = planned.result, planned.world
    console.print(
        format_roster(result.optimal, world.names, world.prices, sentiment=planned.sentiment)
    )
    for index, fallback in enumerate(result.fallbacks, start=1):
        console.print(
            f"[dim]fallback {index}: cost {fallback.total_cost:.0f} | obj {fallback.objective:.1f}[/dim]"
        )


def asta_legality(
    rosa: str = typer.Option(..., help="Player ids in the rosa, comma/space separated."),
    season: Season = SEASON,
) -> None:
    """Print which of the 11 Mantra schemi a rosa can field. Read-only."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")

    ids = parse_ids(rosa)
    pool = build_pool({pid: quotazioni[pid].ruoli_codice for pid in ids if pid in quotazioni})
    schemi = fieldable_schemi(pool, build_legality(load_compat()))
    console.print(format_legality(schemi))


def asta_live(
    replay: str = typer.Option("", help="Path to a JSONL of raw room states (a captured room)."),
    league: str = typer.Option(
        "", help="Fantaleague id of a live room — reads its sale ledger (alternative to --replay)."
    ),
    db: int = typer.Option(-1, help="The room's RTDB shard (its `db` field) — required with --league."),
    team: str = typer.Option(..., help="Our team id in the room."),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: Season = SEASON,
    fmt: str = typer.Option(
        "", "--format",
        help="Override the format: mantra or classic. Detected on --league from the room's "
        "own asta_type, then from the recorded corpus; a run where neither answers is "
        "refused rather than guessed. --replay carries no format at all, so it stays "
        "mantra unless told. It selects both the pool and the corpus, so a Classic room "
        "read as Mantra is advised off players it cannot call, priced off another game — "
        "and nothing raises.",
    ),
    teams: CorpusTeams = DEFAULT_NUM_TEAMS,
    credits: CorpusCredits = DEFAULT_NUM_CREDITS,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Render the rolling advisory off a captured replay (``--replay``) or a live room's sale
    ledger (``--league --db``). Read-only either way — the advisory advises, the human bids.

    The live path keys off the ``purchases/<fl>`` ledger (docs/fantalab/06 §10), not
    ``close_auction``, over the unauthenticated RTDB, and drives the exact same engine off
    ``AssignmentEvent`` as a replay does. **The ledger still needs no token — only the
    shard.**

    What the format needs is a separate question, and the answer is "whatever it can get".
    `--format` used to default to ``mantra`` here while this docstring's own option help
    stated the harm: a Classic room read as Mantra is advised off players it cannot call and
    priced off another game, and *nothing raises* — the pool, the prices and the listone
    bridge are each legal for the wrong format, so the run exits 0 with a complete advisory
    for a different sport. It is now detected: the room's own ``asta_type`` (one
    authenticated ``POST /fantaleague/fetch``), else the harvested corpus for the same id,
    else ``--format``. Every way the first rung can fail degrades to the second, so a
    machine with no FantaLab session and no encryption key still runs exactly as before.
    A ``--league`` run where **no** rung answers is refused rather than guessed.

    ``--replay`` keeps the stated ``mantra`` default, because a landing row is
    ``{seen_at, auction_id, state}`` and the ``auction/<fl>`` state carries no format —
    there really is nothing to detect from there.
    """
    from pathlib import Path

    from fantabot.adapters.persistence import database_manager
    from fantabot.application.asta_advisory import AdvisoryRequest, build_advisory
    from fantabot.application.plan_request import NoSentimentRows

    # `""` only — never a falsy-tolerant check. `--format ""` reads as "not given", which
    # on `--league` means "detect it"; anything else that is not a game is a typo.
    if fmt not in ("", "mantra", "classic"):
        raise typer.BadParameter("--format must be 'mantra' or 'classic'")

    if bool(league) == bool(replay):
        console.print("[red]Pass exactly one of --league or --replay.[/red]")
        raise typer.Exit(1)

    if league:
        if db < 0:
            console.print("[red]--league needs --db (the room's RTDB shard).[/red]")
            raise typer.Exit(1)
        from fantabot.application.asta_format import FormatUnknown, choose_listone

        # Resolved **before** the ledger read, so a refusal costs no network. Three rungs,
        # most specific first: `--format`, then the room's own `asta_type`, then the corpus.
        # There is no fourth. `mantra` was the fourth and it is the defect: this command's
        # own help text stated the harm and the default committed it, and nothing downstream
        # can raise — the pool, the prices and the bridge are all legal for the wrong game.
        try:
            chosen = choose_listone(
                fmt,
                _declared_format(league, warn=lambda m: console.print(f"[dim]{m}[/dim]")),
                _recorded_format(league),
            )
        except FormatUnknown as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        if chosen.warning:
            console.print(f"[yellow]{chosen.warning}[/yellow]")
        console.print(f"[dim]format: {chosen.listone} ({chosen.provenance})[/dim]")
        fmt = chosen.listone

        from fantabot.adapters.http.fantalab import feed

        events = feed.ledger_events(db, league)
    else:
        # A replay carries no format: `adapters/files/landing.py` writes
        # `{seen_at, auction_id, state}` and the state is the `auction/<fl>` node, which has
        # no such field. So there is nothing to detect and a stated default is the honest
        # answer — the same argument `asta calibrate` makes about naming a corpus.
        fmt = fmt or "mantra"
        rows = parse_replay_lines(Path(replay).read_text(encoding="utf-8").splitlines())
        events = normalize(row.get("state", row) for row in rows)

    # FantaLab identifies players by UUID; everything downstream is keyed by fantacalcio id.
    # Fetched here because the two event sources differ and the bridge does not: `--replay`
    # is developer machinery and stays CLI-only (`tasks/archive/parity-spec.md` T20), which is exactly why
    # `build_advisory` takes `events` rather than reading them — one fold over two sources
    # instead of two folds.
    from fantabot.adapters.http.fantalab import listone

    bridge = listone.fetch()

    # The fold, the world read and the id resolution are `application/asta_advisory`'s since
    # 3.10 — the app drives the same three and this body was the only copy of them.
    # `NoSentimentRows` is caught here and nowhere else: turning a refusal into the exception
    # a Typer body may raise is the translation `sentiment_rows` exists for, and it is the
    # half that stays in `interface/`.
    with database_manager.get_session() as session:
        try:
            advisory = build_advisory(
                session,
                AdvisoryRequest(
                    our_team_id=team,
                    season=season,
                    listone=fmt,
                    as_of=_today(),
                    budget=budget,
                    lam=lam,
                    tilt_k=tilt_k,
                    sentiment=sentiment,
                    sentiment_run=parse_run_date(sentiment_run),
                    num_teams=teams,
                    num_credits=credits,
                ),
                events=events,
                bridge=bridge,
            )
        except NoSentimentRows as exc:
            raise typer.BadParameter(str(exc)) from exc

    if advisory.dropped_sales:
        console.print(
            f"[yellow]{advisory.dropped_sales} sale(s) dropped — the listone does not know "
            f"those players, so they cannot be valued[/yellow]"
        )

    if advisory.result is None:
        console.print("[dim]no sales in the replay[/dim]")
        return

    console.print(format_advisory(advisory.result, advisory.walkaways, advisory.world.names))
    console.print(format_opponents(advisory.rivals, names={}, total_budget=int(budget)))


def asta_room(
    url: str = typer.Argument(..., help="The FantaLab room link, or its fantaleague id."),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Bidding is OFF without it."
    ),
    resolve_only: bool = typer.Option(
        False, "--resolve-only", help="Print the resolved room and exit. Touches no RTDB."
    ),
    budget: float = typer.Option(
        0.0, help="Our starting credits. 0 reads the room's own num_credits."
    ),
    limit: int = typer.Option(40, help="Listone rows rendered."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    poll: float = typer.Option(2.0, help="Seconds between polls."),
    season: Season = SEASON,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
    ceiling_alpha: CeilingAlpha = 1.00,
    bargain_beta: BargainBeta = 0.00,
    bargain_share: BargainShare = 0.10,
    max_bridge_age_hours: float = typer.Option(
        4.0,
        help="Refuse --arm (not the run) when the listone bridge could not be refreshed and "
        "the cached copy is older than this. 4h: strictly tighter than the 5-hour-stale copy "
        "that missed 16 transfer-deadline signings on 2026-08-28 — the incident this guards "
        "against, not a number picked in the abstract.",
    ),
    copilot: bool = typer.Option(True, "--copilot/--no-copilot", help="The LLM pane."),
    brief_top: int = typer.Option(40, help="How many of the plan's targets to pre-brief."),
) -> None:
    """The live room: the listone, the lot, the model's bidding and the rosa, on one screen.

    `fantabot asta room "https://app.fantalab.it/asta?asta=<uuid>"`.

    Not `fantabot <url>`: a root callback with a positional argument makes Click consume the
    first token as that argument, so `fantabot asta bid` would exit 2 with `No such command
    'bid'` and all 22 commands would break. A shell alias recovers the ergonomics:
    `fanta() { fantabot asta room "$1"; }`.

    **Two locks before a credit is spent** — `FANTABOT_AUTO_ACT` *and* `--arm`, both opt-in.
    Ctrl-C once disarms and keeps watching; twice exits. Mid-auction "stop bidding, keep
    showing me the room" is far more often what is wanted than "quit".

    ⚠ The authenticated fetch behind `--resolve-only` had no caller in `src/` before this
    phase. If it fails, `harvest scan --seed` still yields the shard and the room's settings.
    """
    import time

    from fantabot.adapters.files.room_journal import RoomJournal
    from fantabot.adapters.files.stopflag import (
        clear_stop,
        clear_unless_precleared,
        read_stop,
        room_stop_path,
    )
    from fantabot.adapters.http.fantalab import feed, listone, rest
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.adapters.tokens.fantalab_store import FantalabStore
    from fantabot.application.asta_copilot import CopilotWorker, briefs_for
    from fantabot.application.asta_room import (
        RoomFrame,
        RoomRefused,
        resolve_room,
    )
    from fantabot.application.asta_session import (
        STALE_BRIDGE,
        lot_router,
        room_arming,
        session_for,
        stop_poll,
    )
    from fantabot.config import journal_path, live_auto_act, settings
    from fantabot.domain.asta.bid import max_bid
    from fantabot.domain.asta.live import InvitationLink, parse_room_url
    from fantabot.domain.asta.report import listone_rows
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.interface.room_view import error_overlay, render

    try:
        fantaleague_id = parse_room_url(url)
    except InvitationLink as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    cipher = TokenCipher(settings.fantabot_encryption_key)
    with database_manager.get_session() as session:
        store = FantalabStore(session, cipher)
        stored = store.load()
        if stored is None or not stored.user_id:
            console.print(
                "[red]No FantaLab session stored. Run: fantabot auth fantalab-login[/red]"
            )
            raise typer.Exit(code=2)
        our_user_id = stored.user_id
        # Bound while the session is open, and the bearer is resolved inside the adapter
        # rather than read out here: `resolve_room` takes a callable, so neither this
        # frame nor the application layer ever holds a credential.
        fetch = rest.fetcher_from(store)

    try:
        resolved = resolve_room(
            fantaleague_id,
            user_id=our_user_id,
            fetch=fetch,
        )
    except RoomRefused as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    rules, rules_provenance = rules_for_room(
        selection=resolved.number_of_players_selection,
        min_player=resolved.min_player,
        max_player=resolved.max_player,
        min_goalkeepers=resolved.min_goalkeepers,
        min_others=resolved.min_others,
        classic_band=resolved.players_settings_data,
    )
    console.print(
        f"[bold]{resolved.fantaleague_id}[/bold] · shard {resolved.db} · "
        f"{resolved.asta_mode}/{resolved.raise_mode} · "
        f"{resolved.num_teams} teams x {resolved.num_credits} credits · "
        f"seat {resolved.seat.team_name or resolved.seat.fantateam_id} · "
        f"roster {rules.size} ({rules_provenance})"
    )
    if resolve_only:
        return

    # `refresh=True`: prefer a live read at room start over whatever the cache already
    # holds — a five-hour-old copy is what missed 16 transfer-deadline signings on
    # 2026-08-28. A transport failure degrades to the cache rather than crashing here
    # (`listone.fetch`'s own docstring); the age check below is what catches that case.
    bridge = listone.fetch(refresh=True)
    if not bridge:
        console.print("[red]no uuid -> fantacalcio_id bridge; every lot would be unknown[/red]")
        raise typer.Exit(code=1)

    bridge_age = listone.cache_age()
    if bridge_age is None:
        console.print("[dim]listone bridge: age unknown (pre-envelope cache)[/dim]")
    elif bridge_age < 60:
        console.print("[dim]listone bridge: just refreshed[/dim]")
    else:
        console.print(f"[yellow]listone bridge: refresh failed, using a {bridge_age / 3600:.1f}h old cache[/yellow]")

    credits = budget or resolved.budget
    with database_manager.get_session() as session:
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=readings, as_of=_today(), tilt_k=tilt_k,
            callable_ids={str(fid) for fid in bridge.values()},
            num_teams=resolved.num_teams or 8,
            num_credits=int(resolved.num_credits or 500),
            # `resolved.asta_type`, not `... or "mantra"`. `resolve_room` refuses a room
            # that declares no format, so there is nothing left to coerce — and the
            # coercion was what hid it: it turned an unanswered question into Mantra
            # before `build_plan_inputs`' own guard could see it.
            listone=resolved.asta_type,
        )

    # Three locks: the two the operator opens, and the bridge that names the lots — a stale
    # one refuses *arming*, never the run. `room_arming` is where all three are weighed, in
    # `application/`, because the app's room route needs the same answer and a second copy
    # of it would be a second opinion nobody is told about. Its docstring carries the why.
    gate = room_arming(
        arm=arm,
        auto_act=live_auto_act(),
        bridge_age=bridge_age,
        max_bridge_age_hours=max_bridge_age_hours,
    )
    if STALE_BRIDGE in gate.closed:
        assert bridge_age is not None  # only a real age can make that lock shut
        # The wording is the CLI's: this line carries the measured age and the operator's own
        # limit, which is why the lock is named rather than sentenced in `application/`.
        console.print(
            f"[red]arming refused: listone bridge is {bridge_age / 3600:.1f}h old, over the "
            f"{max_bridge_age_hours:.0f}h limit (--max-bridge-age-hours). Watching only.[/red]"
        )

    # `armed` is a list so the SIGINT handler can disarm it without a global.
    armed = [gate.armed]
    if armed[0] and not typer.confirm(
        f"Bid REAL CREDITS in {resolved.fantaleague_id} as "
        f"{resolved.seat.team_name or resolved.seat.fantateam_id}, budget {credits:.0f}?"
    ):
        raise typer.Abort

    router = lot_router(resolved.db, resolved.fantaleague_id)
    journal = RoomJournal(journal_path())
    # `cycle_ms` is measured here, not in `application/` — the clock stays out of that layer.
    #
    # **The clock starts at the top of the poll, not at `cycle`.** It used to start inside
    # `target_of`, which is only reached when a lot is on the block — so `waiting` and
    # `error` rows, the two that mean "the loop is in trouble", were the only rows with no
    # timing at all. One field, one meaning: the whole poll, read included. Nothing is lost
    # by the redefinition — `cycle_ms` postdates the 2026-09-01 evening and is null across
    # every recorded row.
    cycle_started = [time.perf_counter()]

    def _timed_journal(row: Mapping[str, Any]) -> None:
        journal.write({**row, "cycle_ms": round((time.perf_counter() - cycle_started[0]) * 1000, 1)})

    def _timed_read() -> Any:
        """The loop's first act each poll, so it is where the poll's clock starts."""
        cycle_started[0] = time.perf_counter()
        return router.read_lot()[0]

    # The composition is `application/`'s, not this body's: the app's room route drives the
    # same twenty keywords off the same `ResolvedRoom` and the same `PlanInputs`, and none of
    # them raises when it is dropped — a second copy diverges quietly, which is exactly how
    # three commands came to hold three value models.
    # Not `session`: that name is the SQLAlchemy one two blocks up, and a body this long
    # with two meanings for it is one edit from reading the wrong thing.
    room_session = session_for(
        resolved=resolved,
        user_id=stored.user_id,
        bridge=bridge,
        world=world,
        rules=rules,
        budget=credits,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        # A new signing the startup fetch above missed (or one added mid-evening) triggers
        # one rate-limited re-fetch instead of holding on it for the rest of the night —
        # `RoomTracker`'s own guard against a burst of new uuids or a shrunk response.
        bridge_refresh=lambda: listone.fetch(refresh=True),
        ledger=lambda: feed.ledger_events(resolved.db, resolved.fantaleague_id),
        # `cycle_ms` is still measured out here — the clock stays out of `application/`.
        journal=_timed_journal,
    )

    # The cooperative stop, derived rather than told — `room_stop_path`'s own note. A
    # supervised run on Windows gets no signal at all, so this file is the whole of how the
    # app asks a live command to stop; a terminal run has no supervisor and clears its own
    # leftovers, which is what `clear_unless_precleared` is deciding between.
    #
    # **The role describes what this run *does*, not which command started it.** `asta room
    # --arm` is a bidder, and filing it under `watch` put it on the flag the app's read-only
    # watch uses — so a Stop aimed at that watch reached an armed terminal run, and the
    # watch's own `ProcessJob.start` cleared the flag the armed run was polling. That is the
    # collision `ROOM_ROLES` exists to prevent, arrived at by naming the role after the verb
    # in the command instead of after the act.
    stop_flag = room_stop_path(
        journal_path(), resolved.fantaleague_id, "bid" if armed[0] else "watch"
    )
    clear_unless_precleared(stop_flag)

    #: The last painted screen, so `on_error` can redraw it under a banner. One slot, not a
    #: log: a renderable per poll for three hours is held by a process that must not die
    #: mid-auction. The frame buffer the budget and cap guards read is `AstaSession.run`'s —
    #: this one is paint, and paint is what stays here.
    screen: list[RenderableType] = []

    # Out of band, on a daemon thread. `counter_time` is 7-10 s and a query takes seconds, so
    # asking about the lot on the block would answer about a lot that has already closed —
    # the targets are briefed ahead and looked up when they come up.
    worker = CopilotWorker() if copilot else None
    briefed: set[str] = set()
    if worker is not None:
        worker.start()

    def paint(frame: RoomFrame) -> None:
        """The frame, drawn. Everything below this line is Rich or the copilot.

        It is handed every frame the session builds, which is the shape that closes 3.6a's
        gap: the decision no longer travels back out through a body that could drop it —
        `AstaSession.run` keeps the answer and passes the picture.
        """
        advice = None
        if worker is not None:
            fresh = [pid for pid in list(frame.walkaways)[:brief_top] if pid not in briefed]
            if fresh:
                briefed.update(fresh)
                worker.brief(
                    briefs_for(
                        fresh,
                        names=dict(world.names), teams=dict(world.teams),
                        roles={k: list(v) for k, v in world.roles.items()},
                        walkaways=dict(frame.walkaways), prices=dict(world.prices),
                        credits_left=frame.credits_left,
                        slots_left=rules.size - len(frame.owned),
                        schemi_open=frame.schemi_open,
                        recent=frame.recent,
                    )
                )
            lot_fid = bridge.get(frame.lot_id) if frame.lot_id else None
            advice = worker.advice_for(str(lot_fid)) if lot_fid else None

        rows = listone_rows(
            cast("Sequence[MantraPlayer]", world.pool),
            AstaState(owned=frame.owned, total_budget=credits),
            names=world.names, teams=world.teams, prices=world.prices, value=world.value,
            walkaways=frame.walkaways, limit=limit,
        )
        view = render(
            frame, rows, room=resolved, armed=armed[0], advice=advice,
            copilot_offline=worker is not None and worker.offline,
        )
        screen[:] = [view]
        live.update(view)

    def on_error(exc: Exception, consecutive: int) -> None:
        """A failed poll, shown on the screen the operator is actually looking at.

        The journal row is `AstaSession.run`'s — both surfaces need it, and a run reporting a
        stall used to leave no record of why. This banner is the terminal's alone.
        """
        live.update(
            error_overlay(
                screen[0] if screen else None,
                f"{type(exc).__name__}: {exc}",
                consecutive=consecutive,
            )
        )

    from rich.live import Live

    # First Ctrl-C disarms and keeps drawing; second exits — `_disarm_on_sigint`, the one
    # handler both live commands share. It was inline here, and `asta bid` never had it;
    # its restore was the line after the loop rather than a `finally`. Entered here rather
    # than before the copilot starts: nothing between the two blocks or reads the network.
    # The cleanup stays inside, so a Ctrl-C during `worker.stop()` is still the handler's.
    try:
        with _disarm_on_sigint(armed), Live(console=console, screen=True, refresh_per_second=4) as live:
                report = room_session.run(
                    # A callable: `LotRouter.node` is rewritten by every read, and a raise must
                    # go back to the node its own lot came from.
                    node=lambda: router.node,
                    read=_timed_read,
                    # Bound per call, not once: `armed[0]` is what the first Ctrl-C clears, and a
                    # writer captured at loop start would keep bidding after the operator disarmed.
                    write=lambda payload: bid_writer(
                        auto_act=live_auto_act(),
                        # `armed[0]` **and** the flag. The first is a decision taken at the top
                        # of this cycle; the second is what can have changed since — and on Windows
                        # it is the whole stop, because `ProcessJob._signal` sends nothing there. A
                        # stop written just after `keep_going` returned True was otherwise honoured
                        # only at the next cycle, which at a lot change is up to 72 s away.
                        arm=armed[0] and read_stop(stop_flag) is None,
                        send=router.write_raise,
                        node=router.node,
                    )(payload),
                    now=lambda: int(time.time() * 1000),
                    sleep=time.sleep,
                    on_frame=paint,
                    on_error=on_error,
                    # Only what the guards read before the first frame exists; from the first
                    # poll on they read the frame.
                    fallback_budget=int(credits),
                    fallback_cap=max_bid(int(credits), rules.size),
                    # The other half of the two-stage gesture: Ctrl-C from the keyboard,
                    # the flag from a supervisor that has no keyboard to press.
                    keep_going=stop_poll(
                        read_stage=lambda: read_stop(stop_flag),
                        armed=armed,
                        announce=console.print,
                    ),
                    poll_seconds=poll,
                )

    finally:
        # **In a `finally`, because these three ran only on the happy path.** Anything
        # escaping `run` — a `KeyboardInterrupt` landing inside `keep_going`, which is
        # evaluated *outside* `run_bid_loop`'s own try — leaked the journal handle, left the
        # copilot thread alive, and left the flag holding `exit` for the next run to read as
        # its operator's second click.
        if worker is not None:
            worker.stop()
        journal.close()
        clear_stop(stop_flag)
    _report_stopped(report)


def asta_calibrate(
    alpha: list[float] = typer.Option(
        [], "--alpha", help="Repeatable. Default sweeps 0.85 0.90 0.95 1.00 1.05 1.10 1.15."
    ),
    teams: CorpusTeams = DEFAULT_NUM_TEAMS,
    credits: CorpusCredits = DEFAULT_NUM_CREDITS,
    season: Season = SEASON,
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion, as the live commands use."),
    fmt: str = typer.Option(
        "mantra", "--format",
        help="Which recorded corpus to sweep: mantra or classic. There is no lega to detect "
        "from — a replay is a corpus, not a league — so this is stated, not detected.",
    ),
) -> None:
    """Replay recorded aste at several ceiling premiums. Read-only, no network.

    The evidence SPEC A6 gates arming on. `--ceiling-alpha` is the premium applied on top of
    `lot_ceiling`'s own re-solved number, and it is hand-set; this replays it against auctions
    that really happened and prints what each value would have spent. Pick the alpha whose
    spend lands near the budget with a rosa that can still field a schema, and paste the table
    into `tasks/archive/parity-todo.md`.

    **Either corpus.** Both reads took the Mantra default, which agreed — so the sweep it ran
    was sound and no Classic sweep could be asked for at all. That mattered because the
    unreachable corpus is the **larger** one: at 8x500 the database holds 259 Classic rooms
    over 32,101 sales against 48 Mantra rooms over 6,466. `--ceiling-alpha` is one number
    shared by both formats and it was calibrated on the smaller evidence.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.adapters.persistence.repositories.aste import AsteRepository
    from fantabot.application.asta_calibrate import HEADER, Lot, RecordedAuction, sweep
    from fantabot.domain.classic.state import ClassicRosterRules

    if fmt not in ("mantra", "classic"):
        console.print(f"[red]--format {fmt}: not a format. Use mantra or classic.[/red]")
        raise typer.Exit(code=2)

    alphas = list(alpha) or [0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15]
    # The band the replay fills, and what `admits` measures a recorded evening against. A
    # Classic corpus swept against `RosterRules()` would drop every room with fewer than 30
    # lots for needing a roster Classic does not have.
    rules = ClassicRosterRules() if fmt == "classic" else RosterRules()

    with database_manager.get_session() as session:
        rows = sentiment_rows(NewsSentimentSource(session), enabled=True, run="")
        # The same shape reaches both corpora. It used to reach only the replay one, so a
        # `--teams 10 --credits 1000` sweep graded a 10x1000 corpus against prices averaged
        # from 8x500 rooms — the grader and the thing being graded priced differently.
        world = read_plan_inputs(
            session, season=season, sentiment=rows, as_of=_today(), listone=fmt,
            tilt_k=SentimentWeights().k, num_teams=teams, num_credits=credits,
        )
        corpus = AsteRepository(session).recorded_auctions(
            asta_type=fmt, num_teams=teams, num_credits=credits
        )

    auctions = [
        RecordedAuction(
            asta_id=asta_id,
            lots=tuple(Lot(player_id=pid, price=price, closed_at_ms=at) for pid, price, at in lots),
        )
        for asta_id, lots in corpus
    ]

    table = sweep(
        auctions, alphas,
        pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, rules=rules, budget=float(credits), lam=lam,
    )
    if not table:
        console.print("[red]no alphas to sweep[/red]")
        raise typer.Exit(code=1)

    first = table[0]
    console.print(
        f"corpus: {fmt}, {teams}x{credits} — {first.auctions} of "
        f"{first.auctions + first.dropped} auctions admitted "
        f"({first.dropped} dropped: fewer lots than the {rules.size}-man band needs)"
    )
    console.print(f"[dim]{HEADER}[/dim]")
    for row in table:
        console.print(row.line())


def asta_bid(
    league: str = typer.Option(..., help="Fantaleague id of the live room."),
    db: int = typer.Option(..., help="The room's RTDB shard index (its `db` field; see docs/fantalab/06)."),
    team: str = typer.Option(..., help="Our fantateam id — the seat we bid from."),
    user: str = typer.Option(..., help="Our user id — rides on every bid."),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Bidding is OFF without it."
    ),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: Season = SEASON,
    fmt: str = typer.Option(
        "", "--format",
        help="Override the format: mantra or classic. Detected from --lega by default, as "
        "asta optimize does. asta bid is unauthenticated and cannot read the room's own "
        "asta_type, so a Classic room with no lega to detect from must be told — a wrong "
        "format prices and caps against the wrong band.",
    ),
    poll: float = typer.Option(2.0, help="Seconds between polls."),
    lega: Lega = 0,
    size: int = typer.Option(
        0,
        "--size",
        help="Roster size THIS room plays: the total number of players a rosa holds. "
        "asta bid is unauthenticated and cannot read it, so without this the band comes "
        "from --lega — a leghe.fantacalcio league with no relation to the FantaLab room. "
        "It sizes the plan and divides the MAX cap.",
    ),
    teams: CorpusTeams = DEFAULT_NUM_TEAMS,
    credits: CorpusCredits = DEFAULT_NUM_CREDITS,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
    ceiling_alpha: CeilingAlpha = 1.00,
    bargain_beta: BargainBeta = 0.00,
    bargain_share: BargainShare = 0.10,
    max_bridge_age_hours: float = typer.Option(
        4.0,
        help="Refuse --arm (not the run) when the listone bridge could not be refreshed and "
        "the cached copy is older than this. 4h: strictly tighter than the 5-hour-stale copy "
        "that missed 16 transfer-deadline signings on 2026-08-28 — the incident this guards "
        "against, not a number picked in the abstract.",
    ),
) -> None:
    """Chase the advisory's targets in a live room, bidding each up to its walk-away.

    Read → decide → write, behind **two locks that must both be open**: ``FANTABOT_AUTO_ACT``
    in the environment *and* ``--arm`` on this invocation. Either one shut logs the intended bid
    and sends nothing. The env var is process-wide and comes from ``.env``, so on its own it arms
    every run for the rest of the day; ``--arm`` is what makes arming a thing the operator does
    now, deliberately, for this room. Participant only: it bids, it never settles a lot (that is the admin's
    close/confirm). The walk-aways re-plan each cycle off the live ``purchases/`` ledger, so they
    already account for what has been spent. Ctrl-C once stops bidding and keeps watching;
    twice exits — the same gesture as ``asta room``, through the same handler.

    Fully unauthenticated: the shard (``--db``), seat (``--team``) and uid (``--user``) are given,
    and the live RTDB read + bid need no token (docs/fantalab/06 §10). The seat is claimed once,
    interactively; this command never touches the auth'd REST API.

    ⚠ **A trade-off of that, not fixed here**: a lot the admin lets a stood raise fall through
    on (Task 2.2's defect — the ledger records it identically to a routine admin skip) is only
    reattributed when the room's seats and admin uid are known, and those live on
    ``RoomConfig``, reached only through the authenticated fetch this command deliberately
    skips. ``asta room`` (which does authenticate) catches it; this command does not.
    """
    import time

    from fantabot.adapters.files.room_journal import RoomJournal
    from fantabot.adapters.files.stopflag import (
        clear_stop,
        clear_unless_precleared,
        read_stop,
        room_stop_path,
    )

    if fmt not in ("", "mantra", "classic"):
        raise typer.BadParameter("--format must be 'mantra' or 'classic'")

    # Fetched once for the run, not per poll: the mapping changes only when the
    # platform adds a player, and a live room does not want an HTTP round trip it
    # can avoid. See `fantalab/listone.py` for why this exists at all.
    #
    # Before the plan, not after: it is now an *input* to the plan, not only a translation
    # for the ledger. The pool has to be narrowed to players the room can actually call.
    from fantabot.adapters.http.fantalab import feed, listone
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.application.asta_room import RoomFrame
    from fantabot.application.asta_session import lot_router, session_from, stop_poll
    from fantabot.config import journal_path, live_auto_act
    from fantabot.domain.asta.bid import Seat, max_bid

    # `refresh=True`: see `asta_room`'s identical fetch for why. A transport failure
    # degrades to the cache (`listone.fetch`'s own docstring); the age check below, before
    # arming is decided, is what catches that case here too.
    bridge = listone.fetch(refresh=True)
    if not bridge:
        console.print(
            "[red]no uuid -> fantacalcio_id bridge. Without it every lot we win is an "
            "unknown player and the planner refuses the roster.[/red]"
        )
        raise typer.Exit(code=1)

    bridge_age = listone.cache_age()
    if bridge_age is None:
        console.print("[dim]listone bridge: age unknown (pre-envelope cache)[/dim]")
    elif bridge_age < 60:
        console.print("[dim]listone bridge: just refreshed[/dim]")
    else:
        console.print(f"[yellow]listone bridge: refresh failed, using a {bridge_age / 3600:.1f}h old cache[/yellow]")

    # **The band and the format first, then the world it selects.** These two blocks used to
    # run the other way round: `read_plan_inputs(listone=fmt)` chose the pool *and* the
    # corpus from the pre-detection value, and `_lega_rules` rebound `fmt` afterwards — so a
    # Classic lega detected here was already being priced off the Mantra listone. One run,
    # two formats, and nothing raised.
    #
    # `max_cap` and the plan both size off the band, so a wrong band caps against the wrong
    # rosa — and this is the command that spends credits. Read from the lega since 2.1,
    # where it was a bare `RosterRules()` (size 30) whatever the lega declared; on
    # 2026-09-02 that lega declared 25/32.
    from contextlib import ExitStack as _ExitStack

    with _ExitStack() as _stack:
        try:
            room_rules, roster_provenance, fmt = _lega_rules(
                lega,
                fmt,
                session=lambda: _stack.enter_context(database_manager.get_session()),
                warn=lambda note: console.print(f"[yellow]{note}[/yellow]"),
                size=size,
            )
        except ValueError as exc:
            # `resize_band`'s refusal. Turning it into an exit code is this body's half —
            # the alternative is `optimize_roster` raising once per two-second cycle, from
            # inside a live loop, for an evening.
            raise typer.BadParameter(str(exc)) from None

    # The same value model asta optimize planned with, by construction now rather than by
    # maintenance: a walk-away is "what is he worth to us", and this is the one command
    # where that number becomes money. On plain fvm this loop would chase Yildiz to 62
    # credits with a metatarsal fracture reported by three sources.
    #
    # `callable_ids` narrows the pool to what FantaLab's listone carries. 41 of 570 players
    # are absent from it and can never come up for auction, yet the optimizer planned around
    # them; in one simulated mid-auction state the top walk-away of all twelve targets was
    # Lukaku, who could not appear on the block at all.
    with database_manager.get_session() as session:
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session,
            season=season,
            sentiment=readings,
            as_of=_today(),
            tilt_k=tilt_k,
            callable_ids={str(fid) for fid in bridge.values()},
            # The **detected** format, which is the whole point of the reordering above.
            listone=fmt,
            # `asta bid` is unauthenticated and cannot read the room's own shape any more
            # than it can read its `asta_type` — which is why `--format` exists. A wrong
            # shape prices against somebody else's game as surely as a wrong format does.
            num_teams=teams,
            num_credits=credits,
        )

    if not world.pool:
        console.print(f"[red]no {fmt} players for season {season} — cannot bid.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]roster band: {getattr(room_rules, 'size', '?')} players ({roster_provenance})[/dim]"
    )

    seat = Seat(fantateam_id=team, user_id=user)

    # A stale bridge (the refresh above failed and fell back) refuses *arming*, not the run
    # — see `asta_room`'s identical guard. `bridge_age is None` (a pre-envelope cache) is not
    # penalised: there is no age to compare yet.
    if listone.is_stale(bridge_age, max_hours=max_bridge_age_hours):
        assert bridge_age is not None  # `is_stale` only returns True with a real age
        console.print(
            f"[red]arming refused: listone bridge is {bridge_age / 3600:.1f}h old, over the "
            f"{max_bridge_age_hours:.0f}h limit (--max-bridge-age-hours). Watching only.[/red]"
        )
        arm = False

    # A list so the SIGINT handler can clear it without a global, and read by the writer on
    # every bid. This was a plain `arm` bool captured once by the write closure, with no
    # handler installed at all: Ctrl-C went straight to `run_bid_loop`'s
    # `except KeyboardInterrupt` and ended the run. The one command that places real raises
    # was the one live command that could not be told "stop bidding, keep watching".
    #
    # The ambient lock is read once for the banner and again on every write by
    # `live_auto_act`; `armed` is the per-invocation half, which only a Ctrl-C can clear.
    auto_act_now = live_auto_act()
    armed = [bool(auto_act_now and arm)]

    # Said before the first poll, not after: the operator has to be able to tell an armed run
    # from a rehearsal at a glance, and the heartbeat that follows looks identical either way.
    if armed[0]:
        console.print(
            "[bold red]● ARMED — bids are real credits. "
            "Ctrl-C once stops bidding and keeps watching; twice exits.[/bold red]"
        )
    else:
        why = "--arm not given" if auto_act_now else "FANTABOT_AUTO_ACT is false"
        console.print(f"[dim]DRY RUN — nothing will be sent ({why})[/dim]")

    journal = RoomJournal(journal_path())
    # `cycle_ms` measured here, not in `application/` — see `asta_room`'s identical wiring,
    # including why the clock starts at the top of the poll rather than at `cycle`.
    cycle_started = [time.perf_counter()]

    def _timed_journal(row: Mapping[str, Any]) -> None:
        journal.write(
            {**row, "cycle_ms": round((time.perf_counter() - cycle_started[0]) * 1000, 1)}
        )

    def _timed_read() -> Any:
        """The loop's first act each poll, so it is where the poll's clock starts."""
        cycle_started[0] = time.perf_counter()
        return router.read_lot()[0]

    # Both nodes, not just `auction/`. Under ASSEGNA random the lot lands on `assign/<fl>`
    # and a bidder watching only the first sees an empty room all evening (docs/fantalab/06
    # §10.6). The node travels with the lot so the raise goes back where it came from.
    #
    # Built in `application/`, like the session: binding `place_raise` to a shard is a write,
    # and `tests/test_layers.py`'s T-spine rule kept it on the ratchet until it moved.
    router = lot_router(db, league)

    # One composition, not two — `session_from` rather than `session_for`, because this
    # command is unauthenticated by design and has no `ResolvedRoom` to read a chair, an
    # admin uid or a countdown off. Those four are stated as `None` here rather than left to
    # a default, which is the whole reason the factory takes them as required keywords: each
    # degrades *silently*, and a passed lot the admin let stand is invisible to this command
    # the same way it always was — `RoomTracker` degrades to that, not to a crash.
    #
    # `bridge_refresh` has no such barrier: the listone endpoint needs no token, so this
    # command gets the same mid-evening re-resolution `asta room` does.
    bid_session = session_from(
        seat=seat,
        fantaleague_id=league,
        admin_user_id=None,
        seat_by_user=None,
        counter_time=None,
        counter_time_first=None,
        bridge=bridge,
        world=world,
        rules=room_rules,
        budget=budget,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        bridge_refresh=lambda: listone.fetch(refresh=True),
        ledger=lambda: feed.ledger_events(db, league),
        journal=_timed_journal,
    )

    # The cooperative stop, derived rather than told. **This is the half 3.9a carried and
    # 3.7 recorded**: on Windows `ProcessJob._signal` sends nothing, so a supervised
    # `asta bid` that did not poll a flag could only be stopped by the grace timer's kill —
    # on the one command that spends credits. `"bid"` and not `"watch"`: a stop aimed at a
    # watch on the same room must not end the bidding.
    stop_flag = room_stop_path(journal_path(), league, "bid")
    clear_unless_precleared(stop_flag)

    # The paint, and only the paint. The fold, the `latest` buffer, the two trouble rows and
    # the loop itself are the session's — this command used to carry its own copy of all four,
    # and `CLAUDE.md` records where that leads twice over: the copy that falls behind is the
    # one that spends credits.
    reported: set[str] = set()

    def on_frame(frame: RoomFrame) -> None:
        """Said once rather than once per poll: at a 2 s cycle the same line would scroll the
        heartbeat away inside a minute, and the heartbeat is all the operator is reading."""
        if frame.note and frame.note not in reported:
            reported.add(frame.note)
            console.print(f"[yellow]{frame.note}[/yellow]")

    def on_error(exc: Exception, consecutive: int) -> None:
        """Shown here, journaled by the session. `run_bid_loop`'s own fallback only ever
        printed, so a failed poll left a line that scrolled away and no record."""
        console.print(f"[red]{type(exc).__name__}: {exc} ({consecutive} in a row)[/red]")

    try:
        with _disarm_on_sigint(armed):
            report = bid_session.run(
                node=lambda: router.node,
                read=_timed_read,
                # Bound per call for the same reason as the live room above: the ambient lock is
                # re-read on every write, so editing `.env` mid-evening disarms this loop too.
                # `armed[0]`, not `arm`: read per bid, so the first Ctrl-C holds the very next raise.
                write=lambda payload: bid_writer(
                    auto_act=live_auto_act(),
                    # `armed[0]` **and** the flag. The first is a decision taken at the top
                    # of this cycle; the second is what can have changed since — and on Windows
                    # it is the whole stop, because `ProcessJob._signal` sends nothing there. A
                    # stop written just after `keep_going` returned True was otherwise honoured
                    # only at the next cycle, which at a lot change is up to 72 s away.
                    arm=armed[0] and read_stop(stop_flag) is None,
                    send=router.write_raise,
                    # The node the lot came from, as `asta room` has always passed. Without it a
                    # dry-run `BidOutcome` for an ASSEGNA lot is recorded as `auction` — forensic
                    # only, and the two live commands should not differ about what they would
                    # have done.
                    node=router.node,
                )(payload),
                now=lambda: int(time.time() * 1000),
                sleep=time.sleep,
                on_frame=on_frame,
                on_error=on_error,
                on_heartbeat=console.print,
                fallback_budget=int(budget),
                fallback_cap=max_bid(int(budget), room_rules.size),
                # Ctrl-C is the keyboard's half of the two-stage gesture; this is a
                # supervisor's, which has no keyboard to press.
                keep_going=stop_poll(
                    read_stage=lambda: read_stop(stop_flag),
                    armed=armed,
                    announce=console.print,
                ),
                poll_seconds=poll,
            )
    finally:
        # See `asta_room`'s identical block: on the happy path only, a `KeyboardInterrupt`
        # from inside `keep_going` leaked the journal and left the flag holding `exit`.
        journal.close()
        clear_stop(stop_flag)
    _report_stopped(report)


def asta_bench(
    replay: Path = typer.Option(
        ...,
        "--replay",
        help=(
            "Directory holding the scenario fixtures (tests/golden/asta_2026_09_01/). Its "
            "parent must hold the golden pool: quotazioni.jsonl, sentiment.jsonl, "
            "clearing_sales.csv, listone_map.json."
        ),
    ),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion, as the live commands use."),
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Replay the 2026-09-01 evening's three problem lots through a real `RoomTracker`.

    **No database, no socket**: every input is the committed golden pool plus the three
    scenario fixtures under `--replay`, exactly as `asta_bench.replay` and
    `test_asta_bench.py` read them. This is `SPEC.md`'s acceptance gate for the asta-fixes
    phase — proof, from one command, that Vicario is never a target, Ostigard holds for free
    on the pre-gate, and Malen prices above the floor and refuses his real clearing price
    (`tasks/archive/parity-spec.md` §8 items 2 and 3; the exact numbers were measured building this command, not
    copied from the spec's own first draft — see `tasks/archive/parity-todo.md` Task 6.2/6.3).

    Exits non-zero, one line per failed invariant, if a change to `asta_room`/`reservation`
    regresses any of the three.
    """
    from fantabot.application.asta_bench import BENCH_SCENARIOS, bench_checks, load_scenario
    from fantabot.application.asta_bench import replay as run_replay
    from fantabot.application.plan_inputs import build_plan_inputs
    from fantabot.domain.asta.prices import Sale, mean_prices
    from fantabot.domain.shared.values import (
        parse_clearing_sales_csv,
        parse_listone_bridge_json,
        parse_quotazioni_jsonl,
        parse_sentiment_jsonl,
    )

    # Same row shapes `tests/_golden.py` reads for the golden harness, parsed by the same
    # functions — `asta bench` and the golden tests must not each grow their own reading of
    # what a `quotazioni.jsonl`/`sentiment.jsonl`/`listone_map.json` row means.
    world_dir = replay.parent
    bridge = parse_listone_bridge_json((world_dir / "listone_map.json").read_text(encoding="utf-8"))
    quotazioni = parse_quotazioni_jsonl((world_dir / "quotazioni.jsonl").read_text(encoding="utf-8"))
    sentiment = parse_sentiment_jsonl((world_dir / "sentiment.jsonl").read_text(encoding="utf-8"))
    sales = parse_clearing_sales_csv((world_dir / "clearing_sales.csv").read_text(encoding="utf-8"))
    prices = mean_prices(Sale(player_id, price) for player_id, price in sales)

    world = build_plan_inputs(
        quotazioni, prices, sentiment,
        as_of=date(2026, 8, 28), tilt_k=tilt_k,
        callable_ids={str(fid) for fid in bridge.values()},
    )

    all_ok = True
    for name, filename, uuid_key, rung_key in BENCH_SCENARIOS:
        scenario = load_scenario(replay, name, filename, uuid_key=uuid_key, rung_key=rung_key)
        rows = run_replay(
            scenario,
            pool=cast("Sequence[MantraPlayer]", world.pool), value=world.value, prices=world.prices, teams=world.teams,
            legality=world.legality, names=world.names, bridge=bridge, lam=lam,
        )
        failures = bench_checks(name, rows)
        if failures:
            all_ok = False
            console.print(f"[red]{name}: FAIL[/red] ({len(rows)} polls)")
            for line in failures:
                console.print(f"  [red]- {line}[/red]")
        else:
            console.print(f"[green]{name}: PASS[/green] ({len(rows)} polls)")

    if not all_ok:
        raise typer.Exit(code=1)


#: `(name, function)`. Explicit, because the group supplies the prefix: the command
#: is `asta optimize`, not `asta asta optimize`.
COMMANDS: tuple[tuple[str, Callable[..., None]], ...] = (
    ("optimize", asta_optimize),
    ("legality", asta_legality),
    ("live", asta_live),
    ("bid", asta_bid),
    ("calibrate", asta_calibrate),
    ("room", asta_room),
    ("bench", asta_bench),
)


def register(asta: typer.Typer) -> None:
    """Attach the asta commands to their group."""
    for name, command in COMMANDS:
        asta.command(name)(command)

"""The hourly refresh: bring the history up to date before the projection plans on it.

Three sources, each owed once per matchday: the **voti** the projection is fitted on, the
**lega** sync that says which round is calculated and who is on the roster, and the **news**
that carries availability. `domain/lineup/refresh.py` decides what is owed; this module
runs it, contains it, and records what happened.

**Containment, per source** (SPEC A19(3)). Every source runs inside a boundary that catches
everything **except** `AssertionError` and `KeyboardInterrupt`, and records the exception's
**type name only** — a message can carry the DSN, and this line is written to a log the
operator pastes. A refresh that raised would take the hourly lineup submit down with it,
which inverts the point: the refresh is the optional half and the submit is the job.

**A success is spent once; a failure is owed again.** `run_refresh` writes the marker after
each source rather than at the end, so a run killed half way keeps what it earned. That is
the same argument as `store_giornata`'s per-giornata commit.

**News is gated off** by `FANTABOT_LINEUP_NEWS` until the supervised launchd proof (A19(5)),
and the gate is read here, at the point of use, failing closed. A disabled source is
`skipped`, not `ok` and not `failed`: it costs nothing, records nothing, and is still owed
the day the setting is turned on.

Nothing here opens a session, a socket or a clock. `now` is a parameter, the sources are a
Protocol, and `LiveRefreshSources` is the one implementation that reaches the outside.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from typing import TYPE_CHECKING, Protocol

from fantabot.application.reporting import Reporter

# `VOTI_GETS_PER_RUN` is imported rather than restated: a second copy of "eight GETs a run"
# is a second thing to keep in step with A20.
from fantabot.domain.lineup.freshness import VOTI_GETS_PER_RUN
from fantabot.domain.lineup.refresh import (
    Marker,
    SourceOutcome,
    due,
    voti_giornate,
    voti_succeeded,
)

if TYPE_CHECKING:
    from fantabot.domain.lineup.refresh import SourceState


@dataclass(frozen=True, slots=True)
class RefreshInputs:
    """Everything a refresh needs to know before it runs. No reads of its own."""

    league_id: int
    season: str
    #: The giornata being planned.
    cmday: int
    #: The day the readings describe, for the news resume key.
    day: date
    #: The lega's roster, for the news run. Empty means news cannot run.
    roster_ids: tuple[int, ...] = ()
    #: `freshness.voti_range` — giornate before `cmday` still short of their fixtures.
    short_giornate: tuple[int, ...] = ()
    #: Whether the lega's round on giornata `cmday - 1` reads calculated.
    previous_calculated: bool = False


def read_refresh_inputs(
    league_id: int,
    *,
    season: str,
    cmday: int,
    day: date,
    competition: int = 0,
) -> RefreshInputs:
    """Assemble what the refresh needs, from the database and (for the roster) the platform.

    Two of the three fields are read from `match_grain`, which is what the voti scrape
    writes — so the refresh asks "what is still missing" of the same table it fills, and
    cannot disagree with itself about whether it worked.

    `previous_calculated` is the **lega's** round, not Serie A's: A20 says voti are final
    once the lega has calculated, and a Serie A giornata whose last match ended an hour ago
    is not that. It is read from the lega's own calendar through `lega.fixtures`.

    The roster is read only when a `competition` is named. News is gated off until T40, and
    a refresh that read the roster it will not use would spend two authenticated GETs an
    hour for a source that does nothing.
    """
    from collections import Counter

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.league import LeagueRepository
    from fantabot.adapters.persistence.repositories.lineup_history import (
        LineupHistoryRepository,
    )
    from fantabot.domain.lineup.freshness import previous_round_calculated, voti_range

    with database_manager.get_session() as session:
        fixtures = LineupHistoryRepository(session).fixtures(season)
        calendar = LeagueRepository(session).fixtures_for(league_id)
    per_giornata = Counter(f.giornata for f in fixtures)
    return RefreshInputs(
        league_id=league_id,
        season=season,
        cmday=cmday,
        day=day,
        roster_ids=_roster_ids(league_id, competition) if competition else (),
        short_giornate=tuple(voti_range(cmday=cmday, fixtures_per_giornata=per_giornata)),
        previous_calculated=previous_round_calculated(calendar, cmday=cmday),
    )


def _roster_ids(league_id: int, competition: int) -> tuple[int, ...]:
    """The lega roster, through the same read the planner uses."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_submit import build_inputs
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher

    cipher = TokenCipher(settings.fantabot_encryption_key)
    with database_manager.get_session() as session:
        inputs, _names, _comp = build_inputs(TokenStore(session, cipher), league_id, competition)
    return tuple(inputs.roster_ids)


class RefreshSources(Protocol):
    """The three things a refresh can do. One method each, and nothing else."""

    def voti(self, inputs: RefreshInputs, giornate: Sequence[int]) -> SourceOutcome: ...

    def lega(self, inputs: RefreshInputs) -> SourceOutcome: ...

    def news(self, inputs: RefreshInputs) -> SourceOutcome: ...


class MarkerStore(Protocol):
    def read(self) -> Marker: ...

    def write(self, marker: Marker) -> None: ...


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """What one refresh did, source by source."""

    cmday: int
    attempted: tuple[str, ...] = ()
    outcomes: tuple[SourceOutcome, ...] = ()
    #: Sources that were already done for this matchday.
    already_done: tuple[str, ...] = ()
    #: Why the marker could not be written, or empty.
    marker_error: str = ""

    @property
    def ok(self) -> bool:
        """No source failed. A skipped source is not a failure."""
        return not any(o.state == "failed" for o in self.outcomes) and not self.marker_error

    def lines(self) -> list[str]:
        """One line per source, markup-free. Nothing here is written by anything remote."""
        out = [f"{o.source}: {o.state}{f' - {o.detail}' if o.detail else ''}" for o in self.outcomes]
        out.extend(f"{name}: already done for g{self.cmday}" for name in self.already_done)
        if self.marker_error:
            out.append(f"marker: not written ({self.marker_error})")
        return out


def run_refresh(
    inputs: RefreshInputs,
    *,
    sources: RefreshSources,
    marker_store: MarkerStore,
    reporter: Reporter,
    now: datetime,
    only: tuple[str, ...] = (),
    force: bool = False,
) -> RefreshReport:
    """Run whatever this matchday still owes, and record what succeeded. Never raises.

    "Never raises" has exactly two exceptions, and they are the two AD8 names: an
    `AssertionError` is a bug and a `KeyboardInterrupt` is the operator. Everything else —
    a site down, a token expired, a database unreachable — is a source that failed and will
    be attempted again next hour.
    """
    marker = marker_store.read()
    wanted = due(marker, cmday=inputs.cmday, only=only, force=force)
    already = tuple(
        s for s in (only or marker.records) if s not in wanted and marker.succeeded(s, inputs.cmday)
    )
    outcomes: list[SourceOutcome] = []
    stamp = now.isoformat()
    marker_error = ""

    for source in wanted:
        outcome = _contained(source, partial(_run_one, source, inputs, sources))
        outcomes.append(outcome)
        reporter.print(f"[dim]{outcome.source}: {outcome.state}[/dim]")
        if not outcome.ok:
            continue
        marker = marker.with_success(
            source, cmday=inputs.cmday, at=stamp, detail=outcome.detail
        )
        # After each source, not at the end: a run killed half way keeps what it earned.
        try:
            marker_store.write(marker)
        except Exception as exc:
            marker_error = type(exc).__name__

    return RefreshReport(
        cmday=inputs.cmday,
        attempted=wanted,
        outcomes=tuple(outcomes),
        already_done=already,
        marker_error=marker_error,
    )


def _run_one(source: str, inputs: RefreshInputs, sources: RefreshSources) -> SourceOutcome:
    if source == "voti":
        giornate = voti_giornate(
            cmday=inputs.cmday,
            short=inputs.short_giornate,
            previous_calculated=inputs.previous_calculated,
            cap=VOTI_GETS_PER_RUN,
        )
        if not giornate:
            return SourceOutcome("voti", "skipped", f"g{inputs.cmday - 1} not calculated yet")
        return sources.voti(inputs, giornate)
    if source == "lega":
        return sources.lega(inputs)
    return sources.news(inputs)


def _contained(source: str, run: Callable[[], SourceOutcome]) -> SourceOutcome:
    """One source, inside the boundary. `AssertionError` and `KeyboardInterrupt` pass."""
    try:
        return run()
    except (AssertionError, KeyboardInterrupt):
        raise
    except BaseException as exc:
        # The **type name only**. A message can carry the DSN, and this line goes to a log.
        return SourceOutcome(source, "failed", type(exc).__name__)


@dataclass
class LiveRefreshSources:
    """The one implementation that reaches the outside. Every import is inside a method."""

    reporter: Reporter
    #: Injected so the tests never touch `Settings`; `None` means "read it at use".
    news_enabled: bool | None = None
    _voti_counts: dict[int, int] = field(default_factory=dict, repr=False)

    def voti(self, inputs: RefreshInputs, giornate: Sequence[int]) -> SourceOutcome:
        """The targeted scrape (T24). A success is rows for `cmday - 1`, nothing less."""
        from fantabot.application.scrape import run_voti_range

        counts = {c.giornata: c.rows for c in run_voti_range(inputs.season, list(giornate))}
        self._voti_counts = counts
        detail = " ".join(f"g{g}:{n}" for g, n in sorted(counts.items()))
        if voti_succeeded(counts, cmday=inputs.cmday):
            return SourceOutcome("voti", "ok", detail)
        return SourceOutcome("voti", "failed", f"no rows for g{inputs.cmday - 1} ({detail})")

    def lega(self, inputs: RefreshInputs) -> SourceOutcome:
        """Collect then persist, in their own sessions — a write transaction must not be
        held open across a multi-megabyte GET (`lega_sync`'s rule)."""
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories.league import LeagueRepository
        from fantabot.adapters.tokens.store import TokenStore
        from fantabot.application.lega_sync import collect, persist
        from fantabot.config import settings
        from fantabot.domain.tokens.crypto import TokenCipher

        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            result = collect(inputs.league_id, store=TokenStore(session, cipher), reporter=self.reporter)
        with database_manager.get_session() as session:
            written = persist(result, LeagueRepository(session))
        detail = " ".join(f"{table}:{n}" for table, n in sorted(written.items()))
        if result.ok:
            return SourceOutcome("lega", "ok", detail)
        return SourceOutcome("lega", "failed", ", ".join(result.failures))

    def news(self, inputs: RefreshInputs) -> SourceOutcome:
        """Gated off until the supervised proof. Disabled means **zero** agent calls."""
        import asyncio

        if not self._news_on():
            return SourceOutcome("news", "skipped", "disabled")
        if not inputs.roster_ids:
            return SourceOutcome("news", "failed", "no roster")

        from fantabot.application.news_roster import PostgresNewsGateway, fetch_roster_news
        from fantabot.config import settings

        result = asyncio.run(
            fetch_roster_news(
                inputs.roster_ids,
                gateway=PostgresNewsGateway(),
                reporter=self.reporter,
                model=settings.resolve_agent_model(""),
                season=inputs.season,
                day=inputs.day,
                write=True,
            )
        )
        state: SourceState = "ok" if result.ok else "failed"
        return SourceOutcome("news", state, result.reason or result.summary())

    def _news_on(self) -> bool:
        """`FANTABOT_LINEUP_NEWS`, parsed at use and failing closed (AD4)."""
        if self.news_enabled is not None:
            return self.news_enabled
        from fantabot.config import Settings

        raw = Settings().fantabot_lineup_news
        return (raw or "").strip().lower() in {"1", "true", "yes", "on"}

"""The pure half of the plan world, in a module that reaches no database.

`PlanInputs` and `build_plan_inputs` lived in `asta_planner.py` beside `read_plan_inputs`,
which imports `AsteRepository` in its body. `tests/_importgraph` counts function-body imports
deliberately — that is where three real violations were hiding — so the whole module reaches
`adapters.persistence`, and so does anything that takes a `PlanInputs`.

That is fine for a command, which reads a database anyway. It is fatal for the live-room
tracker, whose one structural guarantee is that it cannot: hand it a `PlanInputs` and
`test_the_room_module_cannot_reach_postgres` goes red the moment the module is written, with
the fix discovered under deadline.

So the derivation moves here and the two queries stay behind. The split already existed in the
docstring's intent — "`build_plan_inputs` is pure and takes rows" — this makes the import graph
say it too.
"""

from __future__ import annotations

from typing import Any

import _importgraph

from fantabot.application.plan_inputs import build_plan_inputs
from fantabot.domain.asta.optimizer import optimize_roster
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules
from fantabot.domain.shared.values import QuotazioneRow


def test_the_pure_half_cannot_reach_a_database() -> None:
    assert not _importgraph.reaches(
        "fantabot.application.plan_inputs", "fantabot.adapters.persistence"
    )


def test_the_io_half_still_can_because_that_is_its_job() -> None:
    """The guarantee is about where the derivation lives, not about hiding the queries."""
    assert _importgraph.reaches(
        "fantabot.application.asta_planner", "fantabot.adapters.persistence"
    )


def test_the_old_home_still_re_exports_both_names() -> None:
    """Four call sites import them from `asta_planner`, and the golden fixtures are among
    them. Moving the definition is the point; moving the import path as well would be a
    second change riding on the first."""
    from fantabot.application import asta_planner

    assert asta_planner.PlanInputs is not None
    assert asta_planner.build_plan_inputs is not None


class TestTheLeagueShapeIsAParameter:
    """`docs/fantalab/00 §13`: the tool is written for *any* Mantra asta, and our lega is a
    saved profile. "Se un numero o una regola d'asta compare scritto nel codice, è un bug."

    `mantra_clearing_sales(budget=500, num_teams=8)` was written into `read_plan_inputs`. Our
    room happens to be 8x500, so nothing was visibly wrong — and a 10x500 room, of which the
    corpus holds 68, would have been priced off sales from a different game.
    """

    def test_read_plan_inputs_takes_the_shape(self) -> None:
        import inspect

        from fantabot.application.asta_planner import read_plan_inputs

        params = inspect.signature(read_plan_inputs).parameters
        assert "num_teams" in params
        assert "num_credits" in params

    def test_the_defaults_are_our_league_so_no_caller_has_to_change(self) -> None:
        import inspect

        from fantabot.application.asta_planner import read_plan_inputs

        params = inspect.signature(read_plan_inputs).parameters
        assert params["num_teams"].default == 8
        assert params["num_credits"].default == 500


class TestTheClassicWorld:
    """`build_plan_inputs(listone="classic")` builds a Classic pool (single P/D/C/A role each)
    and no schema legality — the Classic auction prices exactly the same value model, only the
    composition constraint differs."""

    @staticmethod
    def _classic_rows() -> dict[str, QuotazioneRow]:
        rows: dict[str, QuotazioneRow] = {}
        for role, n, base in (("P", 4, 10), ("D", 12, 20), ("C", 12, 20), ("A", 9, 30)):
            for i in range(n):
                pid = f"{role}{i}"
                rows[pid] = QuotazioneRow(
                    player_id=pid, nome=pid, squadra=pid, ruoli_codice=(role,),
                    ruoli=(role,), fvm=base - i,
                )
        return rows

    def test_classic_listone_builds_a_classic_pool_and_no_legality(self) -> None:
        world = build_plan_inputs(
            self._classic_rows(), {}, None, as_of=None, tilt_k=1.0, listone="classic",
        )
        assert world.legality == {}
        assert all(isinstance(p, ClassicPlayer) for p in world.pool)
        assert {p.role for p in world.pool} == {"P", "D", "C", "A"}  # type: ignore[union-attr]

    def test_the_classic_world_optimizes_to_the_band(self) -> None:
        world = build_plan_inputs(
            self._classic_rows(), {}, None, as_of=None, tilt_k=1.0, listone="classic",
        )
        r = optimize_roster(
            AstaState(total_budget=500.0), world.pool, value=world.value, prices=world.prices,
            teams=world.teams, legality=world.legality, rules=ClassicRosterRules(), lam=0.0,
        ).optimal
        assert len(r) == 25

    def test_mantra_stays_the_default(self) -> None:
        rows = {
            "x": QuotazioneRow(player_id="x", nome="x", squadra="X", ruoli_codice=("POR",),
                               ruoli=("Por",), fvm=10),
        }
        world = build_plan_inputs(rows, {}, None, as_of=None, tilt_k=1.0)
        assert all(isinstance(p, MantraPlayer) for p in world.pool)
        assert world.legality  # the 11 Mantra schemi are built for the default


class TestTheClassicCorpusReachesThePlanner:
    """`read_plan_inputs` read the corpus behind `if listone == "mantra" ... else []`.

    That guard was correct when it was written — no Classic asta had been recorded — and it
    was never re-examined once one had. A Classic run therefore got no prices at all, and
    did so *silently*: an empty mapping is a legal `prices` argument, so nothing raised and
    nothing warned. Measured over the live pool, the plan then bought a 25-man roster for
    25 credits of 500 — the budget constraint has nothing to bind against — and 22 of its
    25 slots differed from the corpus-priced plan.

    Measured 2026-09-05, once the Classic landing zone was re-loaded into this database:
    32,100 Classic sales over 453 players in 259 rooms of our own 8x500 shape — 4.8x the
    Mantra corpus the planner *was* reading. The bigger corpus was the discarded one.

    Asserted through `read_plan_inputs` rather than on the repository, because the guard
    lived here: a repository that takes `asta_type` is no use if its caller still passes a
    constant.
    """

    @staticmethod
    def _rows(listone: str) -> dict[str, QuotazioneRow]:
        """The two formats do not share a role vocabulary, so the fixture cannot either.

        Classic is P/D/C/A, one role each; Mantra is the 12 codes, and `normalize_role`
        raises on anything else. The prices under test are keyed by player id, which is
        the same in both.
        """
        codes = ("D", "A") if listone == "classic" else ("DC", "PC")
        return {
            "1": QuotazioneRow(player_id="1", nome="Uno", squadra="ATA",
                               ruoli_codice=(codes[0],), ruoli=(codes[0],), fvm=20),
            "2": QuotazioneRow(player_id="2", nome="Due", squadra="BOL",
                               ruoli_codice=(codes[1],), ruoli=(codes[1],), fvm=30),
        }

    def _read(self, monkeypatch: Any, listone: str) -> tuple[Any, dict[str, Any]]:
        from fantabot.adapters.persistence.repositories import aste, reference
        from fantabot.application.asta_planner import read_plan_inputs

        seen: dict[str, Any] = {}
        rows = self._rows(listone)

        class _Aste:
            def __init__(self, session: Any) -> None: ...

            def clearing_sales(
                self, *, asta_type: str = "mantra", budget: int = 500, num_teams: int = 8
            ) -> list[tuple[str, int]]:
                seen["asta_type"] = asta_type
                seen["shape"] = (budget, num_teams)
                return [("1", 10), ("1", 20), ("2", 60)]

        class _Reference:
            def __init__(self, session: Any) -> None: ...

            def quotazioni(self, season: str, listone: str) -> dict[str, QuotazioneRow]:
                return rows

            def excluded_player_ids(self) -> frozenset[str]:
                return frozenset()

        monkeypatch.setattr(aste, "AsteRepository", _Aste)
        monkeypatch.setattr(reference, "ReferenceRepository", _Reference)

        world = read_plan_inputs(
            object(),  # type: ignore[arg-type]
            season="2026/27",
            sentiment=None,
            as_of=None,
            tilt_k=1.0,
            listone=listone,
        )
        return world, seen

    def test_a_classic_run_reads_the_classic_corpus(self, monkeypatch: Any) -> None:
        world, seen = self._read(monkeypatch, "classic")

        assert seen["asta_type"] == "classic"
        assert world.prices == {"1": 15.0, "2": 60.0}

    def test_a_mantra_run_still_reads_the_mantra_corpus(self, monkeypatch: Any) -> None:
        world, seen = self._read(monkeypatch, "mantra")

        assert seen["asta_type"] == "mantra"
        assert world.prices == {"1": 15.0, "2": 60.0}

    def test_the_league_shape_is_still_forwarded(self, monkeypatch: Any) -> None:
        """The 8x500 default is our room, not a constant — `docs/fantalab/00 §13`."""
        _, seen = self._read(monkeypatch, "classic")

        assert seen["shape"] == (500, 8)

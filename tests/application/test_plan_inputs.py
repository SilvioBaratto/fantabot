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

import _importgraph


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

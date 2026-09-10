"""The eight decisions of the submit path, out of the Typer body and testable.

`interface/lineup.py::submit` held them inside a command: the two locks, the matchday
refusal, the dry-run exit, the kickoff warning, the `LUP009` walk-down, the confirming
read-back, and the rule that the report comes from the read-back rather than the submit
response. The app needs every one of them, and *"`interface/` holds no decision the app also
needs"*.

Several are **ordered against each other**, and the order is what these tests are mostly
about — the failures they prevent are not "the wrong answer" but "the right answer at the
wrong moment".

No network: `apileague` is faked. This is the path where being wrong submits a lineup, so
nothing here may reach one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from fantabot.application.arming import ARM, AUTO_ACT
from fantabot.application.lineup_submit import (
    ALL_MODULES_REFUSED,
    NO_MATCHDAY,
    NOT_ARMED,
    submit_lineup,
)
from fantabot.domain.lineup.errors import LineupRejected

NOW = datetime(2026, 9, 5, 12, 0, 0)


class _Plan:
    """A `PlannedLineup` in the shape this module reads it."""

    def __init__(self, module: str, *, mday: int = 3, cmday: int = 4) -> None:
        self.module = module
        self.mday = mday
        self.cmday = cmday
        self.starts: list[int] = [1]
        self.bench: list[int] = [2]


class _Api:
    """A fake `apileague`. Records every submit, so "did not act" is checkable."""

    def __init__(self, *, mstr: str = "", refuse: tuple[str, ...] = ()) -> None:
        self.mstr = mstr
        self.refuse = refuse
        self.submitted: list[str] = []
        self.read_backs = 0
        self.status_reads = 0

    def league_status(self, _lid: int, **_k: Any) -> dict[str, Any]:
        self.status_reads += 1
        return {"mstr": self.mstr}

    def teamLineup_submit(self, _lid: int, body: dict[str, Any], **_k: Any) -> None:
        module = str(body.get("mdl", "?"))
        self.submitted.append(module)
        if module in self.refuse:
            raise LineupRejected("LUP009")

    def teamLineup_read(self, _lid: int, _comp: int, **_k: Any) -> dict[str, Any]:
        self.read_backs += 1
        return {"teamLineupDto": {"starts": [1, 2, 3], "ldate": "2026-09-05T10:00:00"}}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Bind a fake platform and a fixed set of plans, and hand back the fake."""

    def wire(api: _Api, plans: list[_Plan]) -> _Api:
        from fantabot.application import lineup_submit
        from fantabot.domain.lineup import payload as payload_module

        monkeypatch.setattr(
            lineup_submit, "build_plans", lambda *_a, **_k: (plans, {1: "Svilar"}, 7)
        )
        monkeypatch.setattr("fantabot.adapters.http.apileague.league_status", api.league_status)
        monkeypatch.setattr(
            "fantabot.adapters.http.apileague.teamLineup_submit", api.teamLineup_submit
        )
        monkeypatch.setattr(
            "fantabot.adapters.http.apileague.teamLineup_read", api.teamLineup_read
        )
        monkeypatch.setattr(payload_module, "build", lambda plan: {"mdl": plan.module})
        return api

    return wire


def _run(**over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "league_id": 4103937,
        "arm": True,
        "auto_act": True,
        "now": lambda: NOW,
    }
    kwargs.update(over)
    return submit_lineup(object(), **kwargs)  # type: ignore[arg-type]


class TestTheMatchdayRefusalComesFirst:
    """It fires **before** the arm check, so a dry run refuses too. A dry run that printed a
    plan the armed run would have refused is a rehearsal of the wrong thing."""

    @pytest.mark.parametrize("armed", [True, False], ids=["armed", "dry run"])
    def test_no_coordinates_refuses_either_way(self, wired, armed: bool) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(), [_Plan("343", mday=0, cmday=0)])

        outcome = _run(arm=armed, auto_act=armed)

        assert outcome.refused == NO_MATCHDAY
        assert api.submitted == [], "it submitted a lineup with no matchday context"

    def test_it_outranks_the_arm_check(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Both would refuse; the *reason* has to be the one the operator can act on."""
        wired(_Api(), [_Plan("343", mday=0, cmday=0)])

        assert _run(arm=False, auto_act=False).refused == NO_MATCHDAY


class TestTheTwoLocks:
    @pytest.mark.parametrize(
        ("arm", "auto_act", "closed"),
        [(False, True, (ARM,)), (True, False, (AUTO_ACT,)), (False, False, (AUTO_ACT, ARM))],
        ids=["arm withheld", "env off", "both shut"],
    )
    def test_a_shut_lock_submits_nothing_and_names_itself(
        self, wired, arm: bool, auto_act: bool, closed: tuple[str, ...]
    ) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(), [_Plan("343")])

        outcome = _run(arm=arm, auto_act=auto_act)

        assert outcome.refused == NOT_ARMED
        assert outcome.arming.closed == closed
        assert api.submitted == []

    def test_a_dry_run_still_carries_the_plan_it_would_have_sent(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The whole point of a dry run: the operator sees the XI before arming."""
        wired(_Api(), [_Plan("352")])

        outcome = _run(arm=False)

        assert outcome.plan is not None
        assert outcome.plan.module == "352"

    def test_arm_has_no_default(self) -> None:
        """A caller that does not say does not act — 3.1's property, at this seam too."""
        import inspect

        assert inspect.signature(submit_lineup).parameters["arm"].default is inspect.Parameter.empty


class TestTheKickoffWarningWarnsAndNeverBlocks:
    """`mstr` is not confirmed to be the lineup deadline, so the platform stays the
    authority. A guess that blocked would lose a matchday to our own caution."""

    def test_past_kickoff_still_submits(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(mstr="2026-09-05T10:00:00"), [_Plan("343")])

        outcome = _run()

        assert outcome.submitted is not None
        assert api.submitted == ["343"]
        assert outcome.past_deadline == "2026-09-05T10:00:00"

    def test_before_kickoff_says_nothing(self, wired) -> None:  # type: ignore[no-untyped-def]
        wired(_Api(mstr="2026-09-05T18:00:00"), [_Plan("343")])

        assert _run().past_deadline is None

    def test_an_unparseable_timestamp_is_not_treated_as_past(self, wired) -> None:  # type: ignore[no-untyped-def]
        """A warning nobody can act on, on every run, is a warning nobody reads."""
        wired(_Api(mstr="nonsense"), [_Plan("343")])

        assert _run().past_deadline is None

    def test_the_warning_is_never_read_on_a_dry_run(self, wired) -> None:  # type: ignore[no-untyped-def]
        """`league_status` is a network read, and a dry run has already decided."""
        api = wired(_Api(mstr="2026-09-05T10:00:00"), [_Plan("343")])

        outcome = _run(arm=False)

        assert outcome.past_deadline is None
        assert api.status_reads == 0, (
            "a dry run reached the platform for a warning it had already decided not to act on"
        )


class TestTheWalkDown:
    """`mantra_schemi.json`'s 4-1-4-1 was wrong and the platform said so live on
    2026-09-02. A refused module is survived, not fatal."""

    def test_it_falls_to_the_next_module(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(refuse=("343",)), [_Plan("343"), _Plan("352")])

        outcome = _run()

        assert api.submitted == ["343", "352"]
        assert outcome.submitted is not None and outcome.submitted.module == "352"
        assert outcome.rejected == (("343", "LUP009"),)

    def test_every_module_refused_is_its_own_outcome(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(refuse=("343", "352")), [_Plan("343"), _Plan("352")])

        outcome = _run()

        assert outcome.refused == ALL_MODULES_REFUSED
        assert outcome.submitted is None
        assert api.submitted == ["343", "352"]
        assert outcome.rejected == (("343", "LUP009"), ("352", "LUP009"))

    def test_a_first_try_success_records_no_rejections(self, wired) -> None:  # type: ignore[no-untyped-def]
        wired(_Api(), [_Plan("343"), _Plan("352")])

        assert _run().rejected == ()


class TestTheReportComesFromTheReadBack:
    """The submit response is what we sent; the read-back is what the platform kept, and
    only the second is evidence."""

    def test_it_reads_the_lineup_back_after_submitting(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(), [_Plan("343")])

        outcome = _run()

        assert api.read_backs == 1
        assert outcome.saved["starts"] == [1, 2, 3]
        assert outcome.saved["ldate"] == "2026-09-05T10:00:00"

    def test_nothing_is_read_back_when_nothing_was_submitted(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(refuse=("343",)), [_Plan("343")])

        outcome = _run()

        assert api.read_backs == 0
        assert outcome.saved == {}

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
    GUARD,
    NO_MATCHDAY,
    NOT_ARMED,
    submit_lineup,
)
from fantabot.domain.lineup.deadline import MATCHDAY_MISMATCH, MATCHDAY_STARTED
from fantabot.domain.lineup.errors import LineupRejected
from fantabot.domain.tokens.errors import ApiTimeout, ApiUnavailable, TokenRejected

NOW = datetime(2026, 9, 5, 12, 0, 0)


class _Plan:
    """A `PlannedLineup` in the shape this module reads it."""

    def __init__(self, module: str, *, mday: int = 3, cmday: int = 4, guard: str = "") -> None:
        self.module = module
        self.mday = mday
        self.cmday = cmday
        self.guard = guard
        self.starts: list[int] = [1]
        self.bench: list[int] = [2]
        self.tid = 5921


class _Api:
    """A fake `apileague`. Records every submit, so "did not act" is checkable."""

    def __init__(
        self,
        *,
        mstr: str = "",
        mday: int = 4,
        refuse: tuple[str, ...] = (),
        read_raises: Exception | None = None,
    ) -> None:
        self.mstr = mstr
        #: The Serie A matchday `mstr` describes — `league_status`'s own `mday`.
        self.mday = mday
        self.refuse = refuse
        #: What the *confirming read* fails with, after the POST has already returned 200.
        self.read_raises = read_raises
        self.submitted: list[str] = []
        self.read_backs = 0
        self.status_reads = 0

    def league_status(self, _lid: int, **_k: Any) -> dict[str, Any]:
        self.status_reads += 1
        return {"mstr": self.mstr, "mday": self.mday}

    def teamLineup_submit(self, _lid: int, body: dict[str, Any], **_k: Any) -> dict[str, Any]:
        module = str(body.get("mdl", "?"))
        self.submitted.append(module)
        if module in self.refuse:
            raise LineupRejected("LUP009", "The formation module is not allowed.")
        # The real one returns "200 and the saved DTO on success" (`apileague.py`). It is
        # what we sent rather than what was kept, so it is not evidence — but it is the only
        # thing left when the confirming read cannot be had.
        return {"teamLineupDto": {"mdl": module, "starts": [9, 9, 9]}}

    def teamLineup_read(self, _lid: int, _comp: int, **_k: Any) -> dict[str, Any]:
        self.read_backs += 1
        if self.read_raises is not None:
            raise self.read_raises
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


class TestThePositionalGuard:
    """A plan the positional guard refuses is skipped **before** it is POSTed. A `-1` cell is
    accepted by the platform, so the platform's answer cannot be the check for it."""

    def test_a_guarded_plan_is_never_posted(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(), [_Plan("442", guard="[6] C: M -1"), _Plan("3412")])

        outcome = _run()

        assert api.submitted == ["3412"]
        assert outcome.submitted is not None and outcome.submitted.module == "3412"

    def test_the_skip_is_recorded_with_its_slot_role_and_cell(self, wired) -> None:  # type: ignore[no-untyped-def]
        wired(_Api(), [_Plan("442", guard="[6] C: M -1"), _Plan("3412")])

        assert _run().rejected == (("442", f"{GUARD} [6] C: M -1"),)

    def test_every_plan_guarded_is_every_module_refused(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(), [_Plan("442", guard="[6] C: M -1")])

        outcome = _run()

        assert outcome.refused == ALL_MODULES_REFUSED
        assert api.submitted == []

    def test_a_dry_run_shows_the_plan_the_armed_run_would_send(self, wired) -> None:  # type: ignore[no-untyped-def]
        """A dry run that printed the guarded plan would rehearse the wrong lineup."""
        wired(_Api(), [_Plan("442", guard="[6] C: M -1"), _Plan("3412")])

        outcome = _run(arm=False)

        assert outcome.plan is not None and outcome.plan.module == "3412"


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


class TestAFailedReadBackDoesNotUnsubmitTheLineup:
    """Missing evidence is an unknown, not a negative.

    The read-back is the evidence and stays the source of the report. It was also an
    unguarded precondition of returning at all: it runs *after* `teamLineup_submit` has
    returned 200, and anything it raised propagated out of `submit_lineup`, discarding the
    outcome that would have carried `submitted=plan`. The lineup was on the platform and
    both surfaces said it was not — the CLI exiting 1, the page rendering "Not submitted".

    A cron wrapper cannot tell "never submitted" from "submitted, evidence unavailable", so
    it re-runs. That retry is idempotent — the same deterministic XI replaces itself — right
    up until the round closes, after which the re-POST is refused and the operator ends the
    matchday believing nothing was fielded when something was.

    Pre-existing, not introduced by the 3.2 lift: `d74321a^`'s Typer body had the same
    read-back inside the same `try`. What the lift added is the surface that states it
    most explicitly.
    """

    def test_a_timeout_on_the_confirming_read_still_reports_the_submit(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(read_raises=ApiTimeout(10)), [_Plan("343")])

        outcome = _run()

        assert outcome.submitted is not None, "a lineup that reached the platform read as unsubmitted"
        assert outcome.submitted.module == "343"
        assert outcome.refused is None
        assert api.submitted == ["343"], "it must not re-POST to get its evidence"

    def test_the_reason_the_evidence_is_missing_is_carried(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Named, not merely absent: an empty `saved` already means "nothing submitted"."""
        api = wired(_Api(read_raises=ApiTimeout(10)), [_Plan("343")])

        outcome = _run()

        assert outcome.unconfirmed
        assert "10s" in outcome.unconfirmed
        assert api.read_backs == 1

    def test_a_rejected_token_on_the_read_does_not_blame_the_credential(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The 401 case, which is worse than the timeout because it accuses.

        A `TokenRejected` from the confirming GET used to surface as `refused` with "run
        `fantabot auth login`" — about a credential the POST had just used successfully one
        call earlier. The operator re-authenticates to fix a lineup that was already saved.
        """
        wired(_Api(read_raises=TokenRejected(4103937)), [_Plan("343")])

        outcome = _run()

        assert outcome.submitted is not None
        assert outcome.refused is None
        assert outcome.unconfirmed

    def test_the_submit_response_is_the_fallback_for_saved(self, wired) -> None:  # type: ignore[no-untyped-def]
        """`teamLineup_submit` returns the saved DTO and it was being discarded.

        It is what we sent, not what was kept, so it is never evidence — but with the
        read-back gone it is the only description of the lineup there is, and `unconfirmed`
        is what says not to trust it as confirmation.
        """
        wired(_Api(read_raises=ApiUnavailable(502)), [_Plan("343")])

        outcome = _run()

        assert outcome.saved.get("mdl") == "343"

    def test_a_good_read_back_is_unchanged_and_says_nothing(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The negative control. `unconfirmed` must be empty when evidence was had, or every
        successful submit would carry a caveat and the field would mean nothing."""
        wired(_Api(), [_Plan("343")])

        outcome = _run()

        assert outcome.unconfirmed == ""
        assert outcome.saved["starts"] == [1, 2, 3], "the read-back is still the source"

    def test_nothing_submitted_carries_no_caveat_either(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The other negative control: a refusal is a known negative, not an unknown."""
        wired(_Api(refuse=("343",)), [_Plan("343")])

        outcome = _run()

        assert outcome.submitted is None
        assert outcome.unconfirmed == ""


class TestAScheduledRunNeverTouchesALineupInPlay:
    """`scheduled=True` is the `launchd` job: nobody reads its warnings, and the operator
    asked for it never to reshuffle a lineup once the matchday has started.

    The start times here sit **days** from `NOW`, which is naive: `scheduled_cutoff` reads a
    naive `now` as the machine's local time, and a test minutes from the boundary would pass
    or fail by which timezone the runner is in.
    """

    def test_after_the_start_an_armed_scheduled_run_sends_nothing(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(mstr="2026-09-01T18:45:00", mday=4), [_Plan("343", cmday=4)])

        outcome = _run(scheduled=True)

        assert outcome.refused == MATCHDAY_STARTED
        assert api.submitted == [], "a scheduled run reshuffled a lineup in play"
        # 20:45 Rome — the kickoff `mstr` posts as 18:45 UTC, shown on the operator's clock.
        assert "20:45" in outcome.detail

    def test_before_the_start_it_submits(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(mstr="2026-09-30T18:45:00", mday=4), [_Plan("343", cmday=4)])

        outcome = _run(scheduled=True)

        assert outcome.submitted is not None
        assert api.submitted == ["343"]

    def test_a_start_for_another_matchday_sends_nothing(self, wired) -> None:  # type: ignore[no-untyped-def]
        api = wired(_Api(mstr="2026-09-30T18:45:00", mday=5), [_Plan("343", cmday=4)])

        outcome = _run(scheduled=True)

        assert outcome.refused == MATCHDAY_MISMATCH
        assert api.submitted == []

    def test_the_cutoff_outranks_the_arm_check(self, wired) -> None:  # type: ignore[no-untyped-def]
        """For the matchday refusal's reason: a dry run that printed a plan the armed run
        would have refused is a rehearsal of the wrong thing."""
        wired(_Api(mstr="2026-09-01T18:45:00", mday=4), [_Plan("343", cmday=4)])

        outcome = _run(scheduled=True, arm=False, auto_act=False)

        assert outcome.refused == MATCHDAY_STARTED

    def test_a_manual_run_after_the_start_still_only_warns(self, wired) -> None:  # type: ignore[no-untyped-def]
        """Unchanged for a human at the keyboard: warns, submits, the platform decides."""
        api = wired(_Api(mstr="2026-09-01T18:45:00", mday=4), [_Plan("343", cmday=4)])

        outcome = _run()

        assert outcome.submitted is not None and api.submitted == ["343"]
        assert outcome.past_deadline

    def test_the_status_is_read_once(self, wired) -> None:  # type: ignore[no-untyped-def]
        """The cutoff and the kickoff warning read the same `league_status`, not two."""
        api = wired(_Api(mstr="2026-09-30T18:45:00", mday=4), [_Plan("343", cmday=4)])

        _run(scheduled=True)

        assert api.status_reads == 1


class TestEveryRunBecomesARecord:
    """Which rows the app paints red is decided here, once, and not by each reader.

    Four states. **submitted** and **unconfirmed** reached the platform — the second could not
    read it back to prove it. **skipped** is a run doing its job: past the start, the wrong
    matchday, nothing opened yet. **failed** is a run that should have submitted and did not.

    A scheduled run that is not armed is a **failure**, not a dry run: the job exists to
    submit, and a shut lock at 18:00 on a Friday is the silent miss the record is for.
    """

    def test_a_confirmed_submit_names_what_went_in(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import SUBMITTED
        from fantabot.application.lineup_submit import run_record

        wired(_Api(mstr="2026-09-30T18:45:00", mday=4), [_Plan("343", cmday=4)])
        outcome = _run(scheduled=True)

        run = run_record(outcome, league_id=4103937, scheduled=True, at="2026-09-12T10:00:00+02:00")

        assert run.status == SUBMITTED
        assert run.module == "343" and run.serie_a_matchday == 4
        assert run.starters == ("Svilar",)

    def test_an_unconfirmed_submit_is_its_own_state(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import UNCONFIRMED
        from fantabot.application.lineup_submit import run_record

        wired(_Api(mstr="2026-09-30T18:45:00", mday=4, read_raises=ApiTimeout(10)), [_Plan("343", cmday=4)])
        outcome = _run(scheduled=True)

        run = run_record(outcome, league_id=4103937, scheduled=True, at="t")

        assert run.status == UNCONFIRMED and "10s" in run.detail

    def test_past_the_start_is_a_skip(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import SKIPPED
        from fantabot.application.lineup_submit import run_record

        wired(_Api(mstr="2026-09-01T18:45:00", mday=4), [_Plan("343", cmday=4)])
        outcome = _run(scheduled=True)

        run = run_record(outcome, league_id=4103937, scheduled=True, at="t")

        assert run.status == SKIPPED and run.code == MATCHDAY_STARTED

    def test_a_scheduled_run_that_is_not_armed_failed_at_its_job(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import FAILED
        from fantabot.application.lineup_submit import run_record

        wired(_Api(mstr="2026-09-30T18:45:00", mday=4), [_Plan("343", cmday=4)])
        outcome = _run(scheduled=True, arm=False, auto_act=False)

        run = run_record(outcome, league_id=4103937, scheduled=True, at="t")

        assert run.status == FAILED and run.code == NOT_ARMED
        assert "FANTABOT_AUTO_ACT" in run.detail, "the record must say which lock is shut"

    def test_every_module_refused_failed(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import FAILED
        from fantabot.application.lineup_submit import run_record

        wired(_Api(mstr="2026-09-30T18:45:00", mday=4, refuse=("343",)), [_Plan("343", cmday=4)])
        outcome = _run(scheduled=True)

        run = run_record(outcome, league_id=4103937, scheduled=True, at="t")

        assert run.status == FAILED and run.code == ALL_MODULES_REFUSED

    def test_a_run_that_raised_is_a_failure_with_its_reason(self) -> None:
        from fantabot.adapters.files.lineup_runs import FAILED
        from fantabot.application.lineup_submit import failed_run

        run = failed_run(
            "TokenMissing", "no stored token for lega 4103937", league_id=4103937,
            scheduled=True, at="t",
        )

        assert run.status == FAILED and run.code == "TokenMissing"
        assert run.detail == "no stored token for lega 4103937"


class TestTheRecordCarriesItsEvidence:
    """Record v2: ids beside the names, the coordinates, the model, and for each plan the
    walk did not keep, what it laid out and what the platform said. Names are for the eye
    and two players can share one; the next `LUP009` is diagnosed from this or not at all."""

    def test_a_submit_records_ids_coordinates_and_its_model(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.application.lineup_submit import INDEXCOMPARE, run_record

        wired(_Api(), [_Plan("343")])

        run = run_record(_run(), league_id=4103937, scheduled=True, at="t")

        assert run.starter_ids == (1,) and run.bench_ids == (2,)
        assert run.competition == 7 and run.tid == 5921
        assert run.model == INDEXCOMPARE == "indexcompare"

    def test_each_refusal_carries_its_starts_and_the_platform_message(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.adapters.files.lineup_runs import LineupRejection
        from fantabot.application.lineup_submit import run_record

        wired(_Api(refuse=("343",)), [_Plan("343"), _Plan("352")])

        run = run_record(_run(), league_id=4103937, scheduled=True, at="t")

        assert run.rejections == (
            LineupRejection(
                module="343",
                code="LUP009",
                message="The formation module is not allowed.",
                starter_ids=(1,),
                starters=("Svilar",),
            ),
        )
        assert run.rejected == ("343 (LUP009)",), "the display line is unchanged"

    def test_a_guard_skip_is_recorded_with_what_it_would_have_sent(self, wired) -> None:  # type: ignore[no-untyped-def]
        from fantabot.application.lineup_submit import run_record

        wired(_Api(), [_Plan("442", guard="[6] C: M -1"), _Plan("3412")])

        (skip,) = run_record(_run(), league_id=4103937, scheduled=True, at="t").rejections

        assert skip.module == "442" and skip.code == f"{GUARD} [6] C: M -1"
        assert skip.message == "" and skip.starter_ids == (1,)

    def test_a_run_that_raised_still_names_its_model(self) -> None:
        from fantabot.application.lineup_submit import INDEXCOMPARE, failed_run

        run = failed_run("TokenMissing", "no token", league_id=4103937, scheduled=True, at="t")

        assert run.model == INDEXCOMPARE
        assert run.starter_ids == () and run.competition is None

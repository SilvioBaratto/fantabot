"""The scheduled run's cutoff: an unattended submit never touches a lineup in play. Pure.

**`mstr` is the first kickoff, in UTC.** Settled 2026-09-18, four ways, after a week of
reading it conservatively as Italian wall-clock time:

  - giornata 1 `16:30` -> 18:30 kickoff, giornata 3 `18:45` -> 20:45, giornata 5 `18:45` ->
    20:45 (Monza-Sassuolo, Friday) — every gap exactly the CEST offset;
  - a lineup **saved at 19:13 Rome on 2026-09-18 was accepted**, so 18:45 Rome was never a
    deadline;
  - `league_snapshot.stopped` was `False` at 14:26 with the giornata-4 lineup open, and `True`
    once giornata 5 had kicked off — so the platform locks at the kickoff, not two hours before;
  - `league_status.sto` is that same lock flag.

The Rome reading was safe but cost two hours: it would have stopped the scheduled run at 18:45
Rome when the lineup was open until 20:45 — and the operator asked for the last run to be
*close to kickoff*, which is exactly the window where a late injury shows up.

A human at the keyboard keeps the old contract — `is_past_deadline` warns and never blocks,
because a guess that blocked would lose a matchday to caution. A `launchd` job has nobody to
read the warning, and the operator asked for it never to reshuffle a lineup already in play.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fantabot.domain.lineup.deadline import (
    MATCHDAY_MISMATCH,
    MATCHDAY_STARTED,
    NO_START_TIME,
    scheduled_cutoff,
)

#: Giornata 5, exactly as the platform posted it on 2026-09-18 — 20:45 Rome, the
#: Monza-Sassuolo kickoff that locked the lineup.
START = "2026-09-18T18:45:00"


def _utc(hour: int, minute: int, *, day: int = 18, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


class TestReadAsUtc:
    def test_a_minute_before_kickoff_it_may_act(self) -> None:
        """18:44Z is 20:44 in Rome — a minute before Monza-Sassuolo kicked off."""
        assert scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(18, 44)) is None

    def test_from_kickoff_it_may_not(self) -> None:
        cut = scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(18, 45))

        assert cut is not None and cut.code == MATCHDAY_STARTED

    def test_it_does_not_stop_two_hours_early(self) -> None:
        """The mutation this pins, and the one the Rome reading *was*.

        At 17:30Z the lineup is open for another 75 minutes — the platform accepted a save at
        19:13 Rome (17:13Z) on the day this was measured. Reading `mstr` as Rome time would
        have refused here, which is the two hours of late team news the operator asked to keep.
        """
        assert scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(17, 30)) is None

    def test_winter_kickoffs_are_not_shifted_by_the_offset_that_changed(self) -> None:
        """November is CET. A reading that hardcoded +02:00 anywhere would be an hour out."""
        winter = "2026-11-06T19:45:00"  # 20:45 in Rome

        before = scheduled_cutoff(
            mstr=winter, status_mday=11, plan_cmday=11, now=_utc(19, 44, day=6, month=11)
        )
        after = scheduled_cutoff(
            mstr=winter, status_mday=11, plan_cmday=11, now=_utc(19, 45, day=6, month=11)
        )

        assert before is None and after is not None and after.code == MATCHDAY_STARTED

    def test_a_start_that_carries_its_own_zone_is_taken_at_its_word(self) -> None:
        cut = scheduled_cutoff(
            mstr="2026-09-18T20:45:00+02:00", status_mday=4, plan_cmday=4, now=_utc(18, 44)
        )

        assert cut is None


class TestTheStartMustBeForThisLineup:
    def test_a_start_posted_for_another_matchday_does_not_license_this_one(self) -> None:
        """The gap after the platform advances to matchday 5 and before the lineup does:
        `mstr` is 5's start, in the future, while the lineup read back is still 4's — in
        play. Acting on 5's clock would reshuffle 4 mid-matchday."""
        cut = scheduled_cutoff(
            mstr="2026-09-18T18:45:00", status_mday=5, plan_cmday=4, now=_utc(10, 0, day=15)
        )

        assert cut is not None and cut.code == MATCHDAY_MISMATCH

    def test_matching_matchdays_before_the_start_may_act(self) -> None:
        assert (
            scheduled_cutoff(
                mstr="2026-09-18T18:45:00", status_mday=5, plan_cmday=5, now=_utc(10, 0, day=15)
            )
            is None
        )


class TestNothingKnownIsNotPermission:
    @pytest.mark.parametrize("mstr", ["", "not a date", "None"])
    def test_an_unreadable_start_refuses(self, mstr: str) -> None:
        """Fail closed. A start that cannot be read cannot promise "not in play yet", and
        the refusal is recorded where the operator looks — a red row, not a quiet guess."""
        cut = scheduled_cutoff(mstr=mstr, status_mday=4, plan_cmday=4, now=_utc(10, 0))

        assert cut is not None and cut.code == NO_START_TIME

    def test_the_reason_names_the_start_and_the_matchday(self) -> None:
        cut = scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(20, 0))

        assert cut is not None
        # Shown on the operator's wall clock (20:45 Rome), not in the UTC it was posted in.
        assert "20:45" in cut.reason and "4" in cut.reason

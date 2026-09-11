"""The scheduled run's cutoff: an unattended submit never touches a lineup in play. Pure.

`mstr` is `league_status`'s matchday start. Against the recorded Serie A kickoffs it is two
hours early on both matchdays that can be checked — `16:30` for giornata 1's 18:30 first
kickoff (`docs/leghe-api.md`), `18:45` for giornata 3's 20:45 (`league_snapshot`). That fits
two readings summer time cannot separate: a lineup deadline two hours before kickoff, or the
kickoff itself in UTC (CEST is UTC+2). **Read as Italian wall-clock time it is at or before
the real kickoff under both**, so that is the reading a run with nobody watching uses. The
first matchday on CET settles it: a one-hour gap means `mstr` is UTC.

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

#: Matchday 4, exactly as the platform posted it on 2026-09-11.
START = "2026-09-11T18:45:00"


def _utc(hour: int, minute: int, *, day: int = 11, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


class TestReadAsItalianWallClock:
    def test_a_minute_before_the_posted_start_it_may_act(self) -> None:
        assert scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(16, 44)) is None

    def test_from_the_posted_start_it_may_not(self) -> None:
        cut = scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(16, 45))

        assert cut is not None and cut.code == MATCHDAY_STARTED

    def test_the_utc_reading_would_have_acted_for_two_more_hours(self) -> None:
        """The mutation this pins. Read as UTC, 17:30Z — 19:30 in Rome, 75 minutes before
        the 20:45 kickoff and 45 past the posted start — still looks like "before"."""
        cut = scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(17, 30))

        assert cut is not None and cut.code == MATCHDAY_STARTED

    def test_it_is_the_italian_zone_and_not_a_fixed_offset(self) -> None:
        """November is CET, UTC+1. A hardcoded `+02:00` stops an hour early in winter."""
        winter = "2026-11-06T19:45:00"

        before = scheduled_cutoff(
            mstr=winter, status_mday=11, plan_cmday=11, now=_utc(18, 44, day=6, month=11)
        )
        after = scheduled_cutoff(
            mstr=winter, status_mday=11, plan_cmday=11, now=_utc(18, 45, day=6, month=11)
        )

        assert before is None, "19:44 CET is before a 19:45 start"
        assert after is not None and after.code == MATCHDAY_STARTED

    def test_a_start_that_carries_its_own_zone_is_taken_at_its_word(self) -> None:
        cut = scheduled_cutoff(
            mstr="2026-09-11T16:45:00+00:00", status_mday=4, plan_cmday=4, now=_utc(16, 44)
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
        cut = scheduled_cutoff(mstr=START, status_mday=4, plan_cmday=4, now=_utc(19, 0))

        assert cut is not None
        assert "18:45" in cut.reason and "4" in cut.reason

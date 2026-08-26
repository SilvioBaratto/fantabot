"""`orphaned` and `render_state` — the display logic, with no session and no key.

That is the point of the module existing: SC 11 says `token-status` must answer
with `FANTABOT_ENCRYPTION_KEY` absent, and SC 12 wants `ORPHANED`. Both are
reachable here without a database, so neither criterion rests on a live stack or
on getting a shell invocation right.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fantabot.tokens.status import TokenStatus, orphaned, render_state

NOW = datetime(2026, 8, 26, tzinfo=UTC)
FINGERPRINT = "4f2a1c8e"


def a_row(
    *,
    league_id: int = 4103937,
    expires_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    key_fingerprint: str = FINGERPRINT,
) -> TokenStatus:
    return TokenStatus(
        league_id=league_id,
        league_name="Legamiallerotaie2",
        key_fingerprint=key_fingerprint,
        issued_at=NOW - timedelta(days=7),
        expires_at=expires_at or NOW + timedelta(days=357),
        captured_at=NOW - timedelta(days=7),
        last_seen_at=last_seen_at or NOW,
        last_verified_at=None,
    )


# --- orphaned -------------------------------------------------------------


def test_no_rows_are_orphaned_when_there_are_none() -> None:
    assert orphaned([]) == set()


def test_a_single_row_can_never_be_orphaned() -> None:
    """SPEC: the only lega you have is the one you just saw."""
    assert orphaned([a_row()]) == set()


def test_equal_stamps_orphan_nothing() -> None:
    """The normal state after any full login."""
    rows = [a_row(league_id=1), a_row(league_id=2), a_row(league_id=3)]

    assert orphaned(rows) == set()


def test_a_row_behind_the_newest_stamp_is_orphaned() -> None:
    rows = [
        a_row(league_id=3584692, last_seen_at=NOW - timedelta(days=175)),
        a_row(league_id=4103937, last_seen_at=NOW),
    ]

    assert orphaned(rows) == {3584692}


def test_several_lagging_rows_are_all_orphaned() -> None:
    rows = [
        a_row(league_id=1, last_seen_at=NOW - timedelta(days=200)),
        a_row(league_id=2, last_seen_at=NOW - timedelta(days=10)),
        a_row(league_id=3, last_seen_at=NOW),
    ]

    assert orphaned(rows) == {1, 2}


def test_orphaning_is_relative_to_the_newest_row_not_to_now() -> None:
    """Nothing here reads a clock: a table nobody has touched in a year is
    not wholesale orphaned, it is merely old."""
    rows = [
        a_row(league_id=1, last_seen_at=NOW - timedelta(days=400)),
        a_row(league_id=2, last_seen_at=NOW - timedelta(days=400)),
    ]

    assert orphaned(rows) == set()


# --- render_state ---------------------------------------------------------


def test_a_healthy_row_reports_its_remaining_days() -> None:
    assert render_state(a_row(), now=NOW, key_fingerprint=FINGERPRINT) == "ok (357d)"


def test_an_expired_row_names_the_date_it_died() -> None:
    row = a_row(expires_at=NOW - timedelta(days=1))

    assert render_state(row, now=NOW, key_fingerprint=FINGERPRINT).startswith("EXPIRED")


def test_a_key_mismatch_names_both_fingerprints() -> None:
    """SC 15. Which key encrypted this row, and which one is in .env."""
    state = render_state(a_row(key_fingerprint="9b30d7a1"), now=NOW, key_fingerprint=FINGERPRINT)

    assert state == f"KEY MISMATCH (row 9b30d7a1, .env {FINGERPRINT})"


def test_an_orphaned_row_reports_when_it_was_last_seen() -> None:
    row = a_row(last_seen_at=datetime(2026, 3, 4, tzinfo=UTC))
    state = render_state(row, now=NOW, key_fingerprint=FINGERPRINT, is_orphaned=True)

    assert state == "ORPHANED — last seen 2026-03-04"


# --- SC 11: it all works with no key at all -------------------------------


def test_every_state_but_the_mismatch_is_reachable_with_no_key() -> None:
    """SC 11. The expiry columns are plaintext for exactly this reason."""
    healthy = render_state(a_row(), now=NOW, key_fingerprint=None)
    expired = render_state(a_row(expires_at=NOW - timedelta(days=1)), now=NOW, key_fingerprint=None)

    assert healthy == "ok (357d)"
    assert expired.startswith("EXPIRED")


def test_a_mismatch_cannot_be_reported_without_a_key_and_does_not_pretend_to() -> None:
    """With no key there is nothing to compare against, so the row is judged on
    its expiry alone rather than being called suspect."""
    row = a_row(key_fingerprint="9b30d7a1")

    assert render_state(row, now=NOW, key_fingerprint=None) == "ok (357d)"


# --- precedence -----------------------------------------------------------


def test_a_key_mismatch_outranks_expiry() -> None:
    """Nothing about the row can be trusted, so the untrustworthiness leads."""
    row = a_row(key_fingerprint="9b30d7a1", expires_at=NOW - timedelta(days=1))

    assert render_state(row, now=NOW, key_fingerprint=FINGERPRINT).startswith("KEY MISMATCH")


def test_expiry_outranks_orphaning() -> None:
    """SPEC is explicit that an orphaned token is still valid; an expired one
    is the one with an action attached."""
    row = a_row(expires_at=NOW - timedelta(days=1), last_seen_at=NOW - timedelta(days=100))
    state = render_state(row, now=NOW, key_fingerprint=FINGERPRINT, is_orphaned=True)

    assert state.startswith("EXPIRED")


@pytest.mark.parametrize("days", [0, 1, 357])
def test_the_day_count_is_the_whole_days_remaining(days: int) -> None:
    row = a_row(expires_at=NOW + timedelta(days=days, seconds=1))

    assert render_state(row, now=NOW, key_fingerprint=FINGERPRINT) == f"ok ({days}d)"


def test_a_token_expiring_exactly_now_is_expired() -> None:
    assert render_state(a_row(expires_at=NOW), now=NOW, key_fingerprint=None).startswith("EXPIRED")

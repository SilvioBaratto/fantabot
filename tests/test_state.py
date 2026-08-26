"""What ``state.py`` does today, pinned before it is replaced.

SPEC criterion 16 asks for this, and the reason is in SPEC's own words: porting
untested code is how behaviour changes go unnoticed. Nothing referenced
``fantabot.state`` from the suite before this file — ``_DEFAULT_STATE``,
``load``'s merge, ``save``'s ``default=str`` and ``storage_state_path`` all had
zero coverage, so a port could have changed any of them and the run would have
stayed green.

Some of what is recorded here is not behaviour worth keeping. It is recorded
because it is behaviour that exists, and the replacement should change it
deliberately rather than by accident.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from fantabot import state


@pytest.fixture(autouse=True)
def state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module at a scratch file so tests never touch data/."""
    path = tmp_path / "nested" / "state.json"
    monkeypatch.setattr(state.settings, "fantabot_state_file", path)
    return path


class TestLoad:
    def test_a_missing_file_yields_the_defaults(self, state_file: Path) -> None:
        assert state.load() == {
            "last_lineup_matchday": None,
            "last_auction_session_id": None,
            "processed_bids": [],
        }

    def test_stored_values_win_over_the_defaults(self, state_file: Path) -> None:
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps({"last_lineup_matchday": 7}), encoding="utf-8")

        assert state.load()["last_lineup_matchday"] == 7

    def test_keys_absent_from_the_file_still_get_their_default(
        self, state_file: Path
    ) -> None:
        """The merge is defaults-first, file-second, per key — not all-or-nothing."""
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps({"last_lineup_matchday": 7}), encoding="utf-8")

        loaded = state.load()

        assert loaded["last_auction_session_id"] is None
        assert loaded["processed_bids"] == []

    def test_unknown_keys_pass_straight_through(self, state_file: Path) -> None:
        """Untyped dict[str, Any]: anything on disk survives a round trip.

        Worth knowing before the port. Typed columns cannot carry an arbitrary
        key, so anything relying on this stops working — nothing does today.
        """
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps({"something_nobody_declared": 1}), encoding="utf-8")

        assert state.load()["something_nobody_declared"] == 1


class TestSave:
    def test_it_creates_the_parent_directory(self, state_file: Path) -> None:
        assert not state_file.parent.exists()

        state.save({"last_lineup_matchday": 3})

        assert state_file.exists()

    def test_a_saved_value_round_trips(self, state_file: Path) -> None:
        state.save({"last_lineup_matchday": 3})

        assert state.load()["last_lineup_matchday"] == 3

    def test_a_date_is_saved_as_a_string_and_does_not_come_back_a_date(
        self, state_file: Path
    ) -> None:
        """The default=str asymmetry. json.dumps stringifies what it cannot
        serialise, and json.loads has no idea it should undo it — so a date goes
        in and a str comes out, silently. Typed columns end this, which is a
        behaviour change rather than a straight port."""
        state.save({"last_lineup_matchday": date(2026, 10, 7)})

        loaded = state.load()["last_lineup_matchday"]

        assert loaded == "2026-10-07"
        assert not isinstance(loaded, date)


class TestKnownQuirks:
    """Recorded because they exist, not because they are worth keeping."""

    def test_two_loads_with_no_file_share_one_processed_bids_list(
        self, state_file: Path
    ) -> None:
        """``dict(_DEFAULT_STATE)`` is a shallow copy, so the list is the *same*
        object every time. Mutating it mutates the module-level default for the
        rest of the process.
        """
        first = state.load()
        second = state.load()

        assert first["processed_bids"] is second["processed_bids"]

    def test_mutating_it_leaks_into_the_module_default(self, state_file: Path) -> None:
        state.load()["processed_bids"].append("leaked")

        try:
            assert state.load()["processed_bids"] == ["leaked"]
        finally:
            state._DEFAULT_STATE["processed_bids"] = []

    def test_processed_bids_survives_only_here_now(self) -> None:
        """It was declared in state.py, reset by auction.py and appended to by
        nothing — persisted state that was never read. auction.py has dropped it
        and auction_bids replaces it; this default is the last trace, and it
        goes when state.py is stripped.
        """
        source = Path("src/fantabot/auction.py").read_text()

        assert "processed_bids" in state._DEFAULT_STATE
        assert "processed_bids" not in source


def test_storage_state_path_comes_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one function that survives the port: auth.py and browser.py need it,
    and neither may end up importing fantabot.db."""
    expected = tmp_path / "storage_state.json"
    monkeypatch.setattr(state.settings, "fantabot_storage_state", expected)

    assert state.storage_state_path() == expected

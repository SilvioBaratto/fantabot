"""T24: `db exclude` and `db exclusions` are printers over one application function.

The lift's own proof. Both bodies held a decision the Asta page needs — what a valid
exclusion is, and the `SELECT id, nome FROM players` join that makes the list readable —
and neither could be reached from the app. These tests assert on what the command does
with the application layer's answer, never on the table it reads: the round trip is
`tests/integration/test_exclusions.py`'s, in the `db` tier.

No socket and no database: `record_exclusion` and `read_exclusions` are patched at the
point the command imports them, which is also where a body that quietly grew its own
copy of either would stop being patched and fail here.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from fantabot.application.exclusions import ExclusionRecorded, ExclusionRow, InvalidExclusion
from fantabot.interface.app import app

runner = CliRunner()

LEAO = ExclusionRow(
    player_id=4344, nome="Leao", reason="left Serie A 2026-08-30", source="goal.com"
)
NAMELESS = ExclusionRow(player_id=999_001, nome=None, reason="a guess", source="")


class FakeSession:
    """Records only the one thing the command does to a session besides reading."""

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """The session the command gets. Every query against it is patched out."""
    from contextlib import contextmanager

    from fantabot.adapters.persistence import database_manager

    fake = FakeSession()

    @contextmanager
    def get_session():  # type: ignore[no-untyped-def]
        yield fake

    monkeypatch.setattr(database_manager, "get_session", get_session)
    return fake


class TestTheList:
    def test_each_row_shows_its_id_name_reason_and_source(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        from fantabot.application import exclusions

        monkeypatch.setattr(exclusions, "read_exclusions", lambda session: [LEAO])
        result = runner.invoke(app, ["db", "exclusions"])

        assert result.exit_code == 0, result.output
        assert "4344" in result.output
        assert "Leao" in result.output
        assert "left Serie A 2026-08-30" in result.output
        assert "goal.com" in result.output

    def test_an_unscraped_id_says_so_rather_than_printing_a_question_mark(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        """`?` was printed for the absent id and for the unknown name alike. One is a
        row to delete and the other a season to scrape."""
        from fantabot.application import exclusions

        monkeypatch.setattr(exclusions, "read_exclusions", lambda session: [NAMELESS])
        result = runner.invoke(app, ["db", "exclusions"])

        assert "(not scraped)" in result.output
        assert "999001" in result.output

    def test_an_empty_table_says_every_player_is_buyable(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        """A fresh install has none, and that is an answer rather than a failure."""
        from fantabot.application import exclusions

        monkeypatch.setattr(exclusions, "read_exclusions", lambda session: [])
        result = runner.invoke(app, ["db", "exclusions"])

        assert result.exit_code == 0
        assert "no exclusions" in result.output


class TestRecordingOne:
    def test_it_confirms_the_name_and_the_running_total(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        """The name is the check that the id typed was the id meant."""
        from fantabot.application import exclusions

        monkeypatch.setattr(
            exclusions,
            "record_exclusion",
            lambda session, player_id, **kw: ExclusionRecorded(
                row=LEAO, exclusions=(LEAO, NAMELESS)
            ),
        )
        result = runner.invoke(
            app,
            ["db", "exclude", "--player", "4344", "--reason", "left Serie A 2026-08-30"],
        )

        assert result.exit_code == 0, result.output
        assert "Leao" in result.output
        assert "2 exclusions in total" in result.output
        # `record_exclusion` deliberately does not commit — the transaction boundary
        # belongs to whoever knows what else is in it, and here that is this command.
        assert session.commits == 1

    def test_an_id_nothing_resolves_is_flagged_and_still_recorded(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        """Flagged, not refused: the id may belong to a season nobody has scraped yet,
        and the row is what the operator asked for. But it is also the one way this
        command silently does nothing, so it must not go by in silence."""
        from fantabot.application import exclusions

        monkeypatch.setattr(
            exclusions,
            "record_exclusion",
            lambda session, player_id, **kw: ExclusionRecorded(
                row=NAMELESS, exclusions=(NAMELESS,)
            ),
        )
        result = runner.invoke(
            app, ["db", "exclude", "--player", "999001", "--reason", "a guess"]
        )

        assert result.exit_code == 0, result.output
        assert "no player with id 999001 has been scraped" in result.output

    def test_a_refused_exclusion_exits_2_and_prints_why(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession
    ) -> None:
        """`--reason ""` passes Typer's required option. The refusal is the application
        layer's, so the browser form and the command refuse the same things."""
        from fantabot.application import exclusions

        def refuse(session: object, player_id: int, **kw: object) -> ExclusionRecorded:
            raise InvalidExclusion("an exclusion needs a reason")

        monkeypatch.setattr(exclusions, "record_exclusion", refuse)
        result = runner.invoke(app, ["db", "exclude", "--player", "4344", "--reason", ""])

        assert result.exit_code == 2, result.output
        assert "an exclusion needs a reason" in result.output
        assert session.commits == 0, "a refused exclusion must not commit anything"

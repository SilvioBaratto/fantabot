"""T25: the two one-shot team commands, once they stopped living in a Typer body.

`db snapshot-team` held the whole use case inline — which endpoint, which parser, which
repository — where nothing but Typer could reach it. `db backfill-teams` held less, but
what it held was wrong: it caught `SQLAlchemyError` only, so the one failure the backfill
actually has (`TeamMappingError`, raised by `ReferenceRepository.backfill_team_names`
on a prefix collision or a code with no name) came out of the *remedy command* as a
traceback.

No socket is opened: `apileague.my_team` is monkeypatched at the module the use case
imports it from, on `test_lega_sync.py`'s pattern. The session is a fake that records
what was added, because `record_team_snapshot` is one `session.add` and a real database
would test SQLAlchemy rather than this.
"""

from __future__ import annotations

from typing import Any

import pytest

from fantabot.application import team_maintenance
from fantabot.domain.shared.club_names import TeamMappingError

#: One `GET /onboarding/v1/league/teams/my` body, abbreviated keys and all.
MY_TEAM = {
    "id": 1, "idu": 9, "n": "Legamiallerotaie", "nu": "silvio",
    "cri": 500, "crs": 474, "cr": 26,
}


class FakeSession:
    """What `record_team_snapshot` needs and nothing else: `add`, and a `commit` that
    records rather than acts — the second is what proves this layer leaves the
    transaction boundary to its caller."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    def commit(self) -> None:
        self.commits += 1


class TestSnapshotTeam:
    def test_it_records_one_row_carrying_the_credits_that_were_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the command: `cri`/`crs`/`cr` off the wire, named, in
        `league_team_snapshot`. Exact values, so a parser wired to the wrong key fails."""
        monkeypatch.setattr(
            team_maintenance.apileague, "my_team", lambda *_a, **_k: MY_TEAM
        )
        session = FakeSession()

        snapshot = team_maintenance.snapshot_team(session, 4103937, store=None)  # type: ignore[arg-type]

        assert (snapshot.league_id, snapshot.team_id, snapshot.nome) == (
            4103937, 1, "Legamiallerotaie",
        )
        assert (snapshot.credits_initial, snapshot.credits_spent, snapshot.credits_remaining) == (
            500, 474, 26,
        )
        assert len(session.added) == 1
        assert (session.added[0].league_id, session.added[0].credits_remaining) == (4103937, 26)

    def test_the_league_id_comes_from_the_caller_not_the_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`teams/my` carries the team id and its owner and no league id at all
        (`domain/shared/league.parse_team_snapshot`). Asking the wrong lega and storing
        the row under the right one is how a snapshot lands on a lega nobody read."""
        seen: list[int] = []

        def my_team(league_id: int, **_k: Any) -> dict[str, Any]:
            seen.append(league_id)
            return MY_TEAM

        monkeypatch.setattr(team_maintenance.apileague, "my_team", my_team)

        snapshot = team_maintenance.snapshot_team(FakeSession(), 3584692, store=None)  # type: ignore[arg-type]

        assert seen == [3584692]
        assert snapshot.league_id == 3584692

    def test_it_does_not_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`application/exclusions.py`'s rule, for its reason: `get_session` commits on
        clean exit, and a function that committed on its caller's behalf would take the
        transaction boundary away from the one place that knows what else is in it."""
        monkeypatch.setattr(
            team_maintenance.apileague, "my_team", lambda *_a, **_k: MY_TEAM
        )
        session = FakeSession()

        team_maintenance.snapshot_team(session, 4103937, store=None)  # type: ignore[arg-type]

        assert session.commits == 0

    def test_a_refused_read_is_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing is caught here. One read, one write: there is no partial outcome to
        report, and a snapshot that silently did not happen is worse than an exception —
        `league_team_snapshot` is append-only, so the gap is permanent."""

        def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("401")

        monkeypatch.setattr(team_maintenance.apileague, "my_team", boom)
        session = FakeSession()

        with pytest.raises(RuntimeError):
            team_maintenance.snapshot_team(session, 4103937, store=None)  # type: ignore[arg-type]

        assert session.added == []


class TestBackfillTeams:
    def test_it_returns_how_many_names_were_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(team_maintenance, "backfill_team_names", lambda _s: 17)

        assert team_maintenance.backfill_teams(FakeSession()) == 17  # type: ignore[arg-type]

    def test_zero_is_an_answer_not_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fresh database, or one scraped listone-first, has no `match_grain` names to
        resolve from. The repository returns 0 deliberately rather than raising."""
        monkeypatch.setattr(team_maintenance, "backfill_team_names", lambda _s: 0)

        assert team_maintenance.backfill_teams(FakeSession()) == 0  # type: ignore[arg-type]

    def test_an_untrustworthy_mapping_becomes_a_named_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`TeamMappingError` reached the Typer body uncaught, which is a traceback on
        the screen of the one command whose entire job is to be the remedy. It is a
        refusal — nothing was written — so it gets a name both surfaces can render."""

        def boom(_s: Any) -> int:
            raise TeamMappingError("no name for code 'PIS'")

        monkeypatch.setattr(team_maintenance, "backfill_team_names", boom)

        with pytest.raises(team_maintenance.NamesUnresolved) as caught:
            team_maintenance.backfill_teams(FakeSession())  # type: ignore[arg-type]

        assert "PIS" in str(caught.value)

    def test_the_refusal_names_a_remedy_that_is_not_this_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`resolve_team_names_or_report` tells the *scraper* to run
        `fantabot db backfill-teams`. Repeating that sentence here would tell an operator
        to run the command they are already running; what is missing is the season's
        fixtures, and `db scrape voti` is what fetches them."""

        def boom(_s: Any) -> int:
            raise TeamMappingError("no name for code 'PIS'")

        monkeypatch.setattr(team_maintenance, "backfill_team_names", boom)

        with pytest.raises(team_maintenance.NamesUnresolved) as caught:
            team_maintenance.backfill_teams(FakeSession())  # type: ignore[arg-type]

        message = str(caught.value)
        assert "db backfill-teams" not in message
        assert "voti" in message

    def test_a_database_outage_is_not_dressed_up_as_a_mapping_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only `TeamMappingError` is translated. A driver error means nobody knows
        whether the mapping is trustworthy, and the remedy is the database, not a scrape."""
        from sqlalchemy.exc import OperationalError

        def boom(_s: Any) -> int:
            raise OperationalError("SELECT 1", {}, Exception("down"))

        monkeypatch.setattr(team_maintenance, "backfill_team_names", boom)

        with pytest.raises(OperationalError):
            team_maintenance.backfill_teams(FakeSession())  # type: ignore[arg-type]

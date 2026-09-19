"""T24 — `GET /db/exclusions` and `POST /db/exclusions`, beside the Asta page.

One implementation, two surfaces: `application/exclusions.py` decides what a valid
exclusion is and resolves the name on each row, and both this and the Typer body are
printers over it. That matters more here than on most screens, because an exclusion is
invisible everywhere else — it removes a player from every plan the bot makes and no
plan says so. A form that accepted what the command refuses would write rows the
command's own list then cannot explain.

The two routes sit on opposite sides of `api/outcomes.py`'s rule.

* **The list is a status read and degrades open.** A database that will not open returns
  an empty list *with `error` set* — never a bare empty list, which is also the true
  answer for a fresh install, and the page has to tell those apart.
* **The write is a decision and fails closed.** `InvalidExclusion` is a 422 carrying the
  refusal; nothing else is caught, so a failed write is a 500 and the operator finds out.
  `auth.py`'s two disconnects took the same decision for the same reason: the session
  context manager commits *inside* any `try` wrapped around it, so a degrade-open handler
  would turn a rolled-back write into a 200 saying the row was recorded.

Zero sockets: `database_manager.get_session` is patched, and the mapping is exercised
directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fantabot.application.exclusions import ExclusionRecorded, ExclusionRow
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.exclusions import to_wire

LEAO = ExclusionRow(
    player_id=4344, nome="Leao", reason="left Serie A 2026-08-30", source="goal.com"
)
NAMELESS = ExclusionRow(player_id=999_001, nome=None, reason="a guess", source="")


@contextmanager
def _session() -> Any:
    yield object()


@pytest.fixture
def db_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "get_session", _session)


@pytest.fixture
def db_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def refuse() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database_manager, "get_session", refuse)


class TestTheMapping:
    """Pure, and the place a field silently stops being sent."""

    def test_every_part_of_the_row_crosses(self) -> None:
        wire = to_wire(LEAO)

        assert wire.player_id == 4344
        assert wire.nome == "Leao"
        assert wire.reason == "left Serie A 2026-08-30"
        assert wire.source == "goal.com"

    def test_an_unresolved_name_crosses_as_null(self) -> None:
        """`null`, not `"?"` and not `""`. The page renders the two differently because
        the remedies differ — delete the row, or scrape the season."""
        assert to_wire(NAMELESS).nome is None


class TestTheList:
    def test_it_answers_with_the_rows_the_application_layer_read(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        from fantabot.application import exclusions

        monkeypatch.setattr(exclusions, "read_exclusions", lambda session: [LEAO, NAMELESS])
        body = TestClient(app).get("/api/v1/db/exclusions").json()

        assert [row["player_id"] for row in body["exclusions"]] == [4344, 999_001]
        assert body["exclusions"][0]["nome"] == "Leao"
        assert body["exclusions"][1]["nome"] is None
        assert body["error"] is None

    def test_an_empty_table_is_an_answer_and_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        from fantabot.application import exclusions

        monkeypatch.setattr(exclusions, "read_exclusions", lambda session: [])
        body = TestClient(app).get("/api/v1/db/exclusions").json()

        assert body["exclusions"] == []
        assert body["error"] is None, "a fresh install is not a failure"

    def test_a_database_that_will_not_open_says_so_rather_than_reading_as_empty(
        self, db_is_down: None
    ) -> None:
        """The distinction the whole of `outcomes.py` exists for: `[]` with no error is
        "every player is buyable", and that is a very different thing to act on."""
        response = TestClient(app).get("/api/v1/db/exclusions")
        body = response.json()

        assert response.status_code == 200
        assert body["exclusions"] == []
        assert body["error"] is not None
        assert "OperationalError" in body["error"]


class TestRecordingOne:
    def test_it_returns_the_row_and_the_list_it_is_now_part_of(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """The refreshed list comes back with the write, so the page renders what the
        table now holds rather than what it hopes it holds."""
        from fantabot.application import exclusions

        monkeypatch.setattr(
            exclusions,
            "record_exclusion",
            lambda session, player_id, **kw: ExclusionRecorded(
                row=LEAO, exclusions=(LEAO, NAMELESS)
            ),
        )
        response = TestClient(app).post(
            "/api/v1/db/exclusions",
            json={"player_id": 4344, "reason": "left Serie A 2026-08-30", "source": "goal.com"},
        )
        body = response.json()

        assert response.status_code == 201
        assert body["recorded"]["nome"] == "Leao"
        assert [row["player_id"] for row in body["exclusions"]] == [4344, 999_001]
        assert body["total"] == 2

    def test_the_application_layer_gets_exactly_what_was_posted(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """The route decides nothing — not a default source, not a trim. A second copy
        of "what a valid exclusion is" is the whole thing this task removed."""
        from fantabot.application import exclusions

        seen: dict[str, Any] = {}

        def spy(session: object, player_id: int, **kw: Any) -> ExclusionRecorded:
            seen.update({"player_id": player_id, **kw})
            return ExclusionRecorded(row=LEAO, exclusions=(LEAO,))

        monkeypatch.setattr(exclusions, "record_exclusion", spy)
        TestClient(app).post(
            "/api/v1/db/exclusions",
            json={"player_id": 4344, "reason": "  left Serie A  ", "source": " goal.com "},
        )

        assert seen == {
            "player_id": 4344,
            "reason": "  left Serie A  ",
            "source": " goal.com ",
        }

    def test_an_omitted_source_is_empty_rather_than_missing(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """`--source` has always been optional; the reason is the required half."""
        from fantabot.application import exclusions

        seen: dict[str, Any] = {}

        def spy(session: object, player_id: int, **kw: Any) -> ExclusionRecorded:
            seen.update(kw)
            return ExclusionRecorded(row=LEAO, exclusions=(LEAO,))

        monkeypatch.setattr(exclusions, "record_exclusion", spy)
        TestClient(app).post(
            "/api/v1/db/exclusions", json={"player_id": 4344, "reason": "left Serie A"}
        )

        assert seen["source"] == ""

    @pytest.mark.parametrize(
        ("body", "expected_in_detail"),
        [
            ({"player_id": 4344, "reason": ""}, "reason"),
            ({"player_id": 4344, "reason": "   "}, "reason"),
            ({"player_id": 0, "reason": "left Serie A"}, "0"),
            ({"player_id": -1, "reason": "left Serie A"}, "-1"),
        ],
    )
    def test_what_the_command_refuses_the_form_refuses(
        self, db_answers: None, body: dict[str, Any], expected_in_detail: str
    ) -> None:
        """Not re-implemented here: `record_exclusion` raises and this turns it into a
        422 carrying the refusal. The refusal's own wording is the application layer's,
        so the page and the terminal say the same thing."""
        response = TestClient(app).post("/api/v1/db/exclusions", json=body)

        assert response.status_code == 422
        assert expected_in_detail in response.json()["detail"]

    def test_a_failed_write_is_a_500_and_not_a_cheerful_200(self, db_is_down: None) -> None:
        """`auth.py`'s two disconnects took this decision first. The session context
        manager commits *inside* any `try` wrapped around it, so a degrade-open handler
        here would report a rolled-back write as a recorded row."""
        with pytest.raises(OperationalError):
            TestClient(app).post(
                "/api/v1/db/exclusions", json={"player_id": 4344, "reason": "left Serie A"}
            )


class TestRemovingOne:
    """`DELETE /db/exclusions/{player_id}` — `fantabot db unexclude`, from the browser.

    It exists now because the command does. §8 Never #4 is about the app having a power
    the CLI lacks, and the CLI got this one first; the route is a printer over the same
    `remove_exclusion` and so refuses exactly what the command refuses.
    """

    def test_it_returns_the_removed_row_and_the_list_without_it(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """The removed row whole, because its reason is the only half nothing else in
        the database holds — a page that drops it makes the removal unreversible."""
        from fantabot.application import exclusions

        monkeypatch.setattr(
            exclusions,
            "remove_exclusion",
            lambda session, player_id: exclusions.ExclusionRemoved(
                row=LEAO, exclusions=(NAMELESS,)
            ),
        )
        response = TestClient(app).delete("/api/v1/db/exclusions/4344")
        body = response.json()

        assert response.status_code == 200
        assert body["removed"]["nome"] == "Leao"
        assert body["removed"]["reason"] == "left Serie A 2026-08-30"
        assert [row["player_id"] for row in body["exclusions"]] == [999_001]
        assert body["total"] == 1

    def test_the_application_layer_gets_the_id_from_the_path(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """The route decides nothing, here least of all: the id it is handed is the id
        whose row disappears."""
        from fantabot.application import exclusions

        seen: dict[str, Any] = {}

        def spy(session: object, player_id: int) -> Any:
            seen["player_id"] = player_id
            return exclusions.ExclusionRemoved(row=LEAO, exclusions=())

        monkeypatch.setattr(exclusions, "remove_exclusion", spy)
        TestClient(app).delete("/api/v1/db/exclusions/4344")

        assert seen == {"player_id": 4344}

    @pytest.mark.parametrize("player_id", [0, -1])
    def test_an_id_no_write_could_produce_today_still_crosses_untouched(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None, player_id: int
    ) -> None:
        """`clean_exclusion` refuses a non-positive id and `db exclude` validated
        nothing at all before T24, so a `0` row is precisely what a removal is the
        remedy for. A route that clamped or absolute-valued the path would leave the
        one row nobody can act on removable from the terminal and not from the page."""
        from fantabot.application import exclusions

        seen: dict[str, Any] = {}

        def spy(session: object, pid: int) -> Any:
            seen["player_id"] = pid
            return exclusions.ExclusionRemoved(row=LEAO, exclusions=())

        monkeypatch.setattr(exclusions, "remove_exclusion", spy)
        TestClient(app).delete(f"/api/v1/db/exclusions/{player_id}")

        assert seen == {"player_id": player_id}

    def test_a_removal_that_matched_nothing_is_a_404_carrying_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch, db_answers: None
    ) -> None:
        """404 rather than the write route's 422, because the two say different things:
        one is a sentence to rewrite, the other an id to look up on the list. Neither is
        a 200 — a delete that matched nothing and reported success is the defect the
        command exists to remove."""
        from fantabot.application import exclusions

        def refuse(session: object, player_id: int) -> Any:
            raise exclusions.ExclusionNotFound(f"no exclusion for id {player_id}")

        monkeypatch.setattr(exclusions, "remove_exclusion", refuse)
        response = TestClient(app).delete("/api/v1/db/exclusions/999002")

        assert response.status_code == 404
        assert "no exclusion for id 999002" in response.json()["detail"]

    def test_a_failed_removal_is_a_500_and_not_a_cheerful_200(
        self, db_is_down: None
    ) -> None:
        """A decision, so it fails closed — the same reason the write does. `get_session`
        commits on clean exit, i.e. inside any `try` wrapped around it, so a degrade-open
        handler would report a rolled-back delete as a removed row."""
        with pytest.raises(OperationalError):
            TestClient(app).delete("/api/v1/db/exclusions/4344")

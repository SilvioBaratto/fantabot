"""The corpus panel's endpoint — what is stored, per format.

The instrument every later collection increment is graded on (`todo/TODO.md` §2, which
says to build it first, and §1.1 for what an unfalsifiable "collection worked" cost).
Read-only, and it degrades open like `/db/health`: this is the endpoint that reports a
down database, so it must not be the endpoint that 500s when the database is down.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.harvest import read_corpus


class FakeAsteRepo:
    def __init__(self, rows: list[object] | None = None, *, raises: bool = False) -> None:
        self._rows = rows or []
        self._raises = raises

    def corpus_summary(self, *, num_credits: int = 500, num_teams: int = 8) -> list[object]:
        if self._raises:
            raise RuntimeError("db unreachable")
        return self._rows


class Row:
    """A `CorpusRow` stand-in — the endpoint reads attributes, not a class."""

    def __init__(self, asta_type: str, **counts: int) -> None:
        self.asta_type = asta_type
        self.rooms = counts.get("rooms", 0)
        self.rooms_with_events = counts.get("rooms_with_events", 0)
        self.events = counts.get("events", 0)
        self.assignments = counts.get("assignments", 0)
        self.assignments_with_buyer = counts.get("assignments_with_buyer", 0)
        self.assignments_with_player = counts.get("assignments_with_player", 0)
        self.planner_sales = counts.get("planner_sales", 0)


def test_read_corpus_carries_every_count_through() -> None:
    corpus = read_corpus(
        FakeAsteRepo([Row("classic", rooms=4386, events=2145179, planner_sales=32100)])
    )

    assert corpus.ok is True
    assert [row.asta_type for row in corpus.formats] == ["classic"]
    assert corpus.formats[0].events == 2145179
    assert corpus.formats[0].planner_sales == 32100


def test_read_corpus_states_the_filter_the_headline_number_survived() -> None:
    """The number is meaningless without it, and a tooltip is not the panel's contract.

    `planner_sales` is `clearing_sales` under `8 x 500` with a buyer and a player link;
    read against another shape it is a different number entirely, so the shape travels
    with the answer rather than being assumed by whoever renders it.
    """
    corpus = read_corpus(FakeAsteRepo([]), num_credits=250, num_teams=10)

    assert corpus.num_credits == 250
    assert corpus.num_teams == 10


def test_read_corpus_degrades_open_when_the_repository_raises() -> None:
    corpus = read_corpus(FakeAsteRepo(raises=True))

    assert corpus.ok is False
    assert corpus.formats == []
    assert corpus.error


def test_the_endpoint_never_500s_when_the_database_is_down(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/harvest/corpus")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["formats"] == []

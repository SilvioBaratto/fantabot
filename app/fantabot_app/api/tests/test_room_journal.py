"""The room journal viewer — the CLI's record of an evening, read back.

`data/room_journal.jsonl` holds 5,192 rows from 2026-09-01 and **nothing anywhere reads
it**: the audit that found all three bidder defects was done by hand against the file, and
the terminal never rendered it either. This endpoint is the first reader.

Three properties are tested here rather than assumed, because each one already cost
something somewhere in this repository:

* **It pages.** 5,192 rows is 1.6 MB of JSON and an evening does not arrive in one
  response.
* **A torn trailing line is skipped, not fatal.** The same rule the landing zone keeps —
  the journal flushes per line, so the one line a crash can tear is the last, and it is
  the newest, which is the first row a tail-first viewer would try to render.
* **A missing journal is not an error.** It is "no journal yet", and it names the path it
  looked at, because `fantabot_data_dir` is relative and resolves against whatever working
  directory the launcher was started in (§3.1's footgun in
  `tasks/archive/fantalab-in-the-app-spec.md`, unmoved here on purpose: the artefact is
  the CLI's).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from fantabot_app.api.main import app
from fantabot_app.api.v1.endpoints.asta import read_journal


def row(**over: object) -> str:
    base: dict[str, object] = {
        "at_ms": 1788290388509,
        "node": "auction",
        "lot": "503cf24d-dbdc-410e-8ce0-f58133032085",
        "name": "Bella-Kotchap",
        "price": 0,
        "walk_away": None,
        "provenance": None,
        "decision": "hold",
        "reason": None,
        "credits_left": 500,
        "max_cap": 471,
        "owned": [],
    }
    base.update(over)
    return json.dumps(base)


def write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_missing_journal_is_not_an_error_and_names_the_path(tmp_path: Path) -> None:
    page = read_journal(tmp_path / "room_journal.jsonl")

    assert page.ok is True
    assert page.exists is False
    assert page.total == 0
    assert page.rows == []
    # The path is the answer, not decoration: it is relative to the launcher's working
    # directory, so "there is no journal" and "you are looking in the wrong place" are
    # the same screen until it says where it looked.
    assert page.path.endswith("room_journal.jsonl")


def test_rows_arrive_newest_first(tmp_path: Path) -> None:
    journal = write(
        tmp_path / "j.jsonl",
        row(name="Oldest", at_ms=1),
        row(name="Middle", at_ms=2),
        row(name="Newest", at_ms=3),
    )

    page = read_journal(journal)

    assert [r.name for r in page.rows] == ["Newest", "Middle", "Oldest"]
    assert page.total == 3


def test_an_evening_does_not_arrive_in_one_response(tmp_path: Path) -> None:
    """5,192 rows is the real file. A page is a page."""
    journal = write(tmp_path / "j.jsonl", *(row(name=f"P{n}", at_ms=n) for n in range(250)))

    first = read_journal(journal, limit=100)
    second = read_journal(journal, offset=100, limit=100)

    assert first.total == 250
    assert len(first.rows) == 100
    assert first.rows[0].name == "P249"
    assert second.rows[0].name == "P149"
    # No overlap: the second page starts where the first stopped, not one short of it.
    assert {r.name for r in first.rows}.isdisjoint({r.name for r in second.rows})


def test_the_line_number_survives_paging(tmp_path: Path) -> None:
    """A row has to be citable back to the file the audit was done against."""
    journal = write(tmp_path / "j.jsonl", *(row(name=f"P{n}") for n in range(10)))

    page = read_journal(journal, offset=0, limit=3)

    assert [r.index for r in page.rows] == [10, 9, 8]


def test_a_torn_trailing_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    """The journal flushes per line, so the only line a crash can tear is the newest.

    Tail-first, that is the *first* row rendered — so a viewer that raises on it shows
    nothing at all for the evening it exists to explain.
    """
    journal = tmp_path / "j.jsonl"
    journal.write_text(
        row(name="Whole", at_ms=1) + "\n" + '{"at_ms": 2, "name": "To',
        encoding="utf-8",
    )

    page = read_journal(journal)

    assert page.ok is True
    assert page.skipped == 1
    assert [r.name for r in page.rows] == ["Whole"]
    assert page.total == 1


def test_a_journal_that_cannot_be_read_reports_rather_than_500s(tmp_path: Path) -> None:
    directory = tmp_path / "room_journal.jsonl"
    directory.mkdir()

    page = read_journal(directory)

    assert page.ok is False
    assert page.error is not None


def test_the_page_carries_what_the_evening_was_decided_on(tmp_path: Path) -> None:
    """The five fields the 2026-09-01 audit was done with, plus the stall's own clock.

    `walk_away` null on 4,501 of 5,192 rows is what B2 looked like in the file, so a
    viewer that dropped nulls would hide the defect it exists to surface.
    """
    journal = write(
        tmp_path / "j.jsonl",
        row(
            name="Holm",
            price=1,
            walk_away=None,
            provenance=None,
            decision="hold",
            reason=None,
            credits_left=29,
            max_cap=27,
            owned=["6898", "6875"],
            cycle_ms=72300.4,
        ),
    )

    (only,) = read_journal(journal).rows

    assert only.name == "Holm"
    assert only.price == 1
    assert only.walk_away is None
    assert only.decision == "hold"
    assert only.credits_left == 29
    assert only.max_cap == 27
    assert only.owned_count == 2
    assert only.cycle_ms == 72300.4


def test_limit_is_bounded_so_one_request_cannot_ask_for_the_evening(tmp_path: Path) -> None:
    journal = write(tmp_path / "j.jsonl", *(row(name=f"P{n}") for n in range(10)))

    page = read_journal(journal, limit=100_000)

    assert page.limit <= 500


def test_the_endpoint_answers_without_a_journal_on_disk() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/asta/journal", params={"limit": 5})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # Absolute, because a relative path is the whole ambiguity this field exists to end.
    assert Path(body["path"]).is_absolute()

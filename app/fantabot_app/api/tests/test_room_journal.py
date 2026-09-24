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
  directory the launcher was started in (§3.1's footgun in the archived
  fantalab-in-the-app-phase spec, unmoved here on purpose: the artefact is
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


def test_a_waiting_row_is_not_a_row_of_nulls(tmp_path: Path) -> None:
    """`waiting` and `error` rows carry two or three keys. Until `error` was read they
    rendered identically — and telling a skipped poll from a crash is `error_row`'s
    entire purpose."""
    journal = write(
        tmp_path / "j.jsonl",
        json.dumps({"at_ms": 1, "decision": "waiting"}),
        json.dumps({"at_ms": 2, "decision": "error", "error": "ReadTimeout"}),
    )

    crash, skipped_poll = read_journal(journal).rows

    assert (crash.decision, crash.error) == ("error", "ReadTimeout")
    assert (skipped_poll.decision, skipped_poll.error) == ("waiting", None)


def test_the_bargain_pair_reaches_the_page(tmp_path: Path) -> None:
    """Written since `44cfe89` — an ancestor of this viewer's own commit `86acb6c` — and
    read by nothing until now. An aggregate cap the operator cannot see after the evening
    is one they only find out about by not understanding why a bid was held."""
    journal = write(
        tmp_path / "j.jsonl", row(decision="hold", bargain_spent=37, bargain_allowance=50)
    )

    (only,) = read_journal(journal).rows

    assert (only.bargain_spent, only.bargain_allowance) == (37, 50)


def test_the_endpoint_reads_the_path_the_cli_writes(tmp_path: Path) -> None:
    """One derived path, not four hand-joined literals: `config.journal_path()`."""
    from fantabot.config import journal_path

    from fantabot_app.api.v1.endpoints import asta as endpoint

    with TestClient(app) as client:
        body = client.get("/api/v1/asta/journal").json()

    assert body["path"] == str(journal_path())
    assert not hasattr(endpoint, "JOURNAL_FILE"), (
        "the endpoint names the journal file itself again — that is the drift `journal_path` closed"
    )


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


# ---------------------------------------------------------------------------------------
# ?follow=1 — the tail. A live room appends every two seconds and a viewer polls it.
# ---------------------------------------------------------------------------------------


def test_a_follow_poll_returns_only_the_rows_written_since_the_last_one(tmp_path: Path) -> None:
    """The whole contract. A viewer that re-read the evening every two seconds would grow
    a 1.6 MB response into a 3-hour poll loop, and render every row it had already drawn."""
    journal = write(tmp_path / "j.jsonl", row(name="A"), row(name="B"), row(name="C"))

    first = read_journal(journal, follow=True, since=0)
    write(journal, row(name="A"), row(name="B"), row(name="C"), row(name="D"), row(name="E"))
    second = read_journal(journal, follow=True, since=first.next_index)

    assert [r.name for r in first.rows] == ["A", "B", "C"]
    assert first.next_index == 3
    assert [r.name for r in second.rows] == ["D", "E"]
    assert second.next_index == 5


def test_the_tail_arrives_oldest_first_which_is_the_opposite_of_the_page(tmp_path: Path) -> None:
    """A page is read from the end of an evening; a tail is appended to a list on a screen."""
    journal = write(tmp_path / "j.jsonl", row(name="Oldest"), row(name="Middle"), row(name="Newest"))

    assert [r.name for r in read_journal(journal).rows] == ["Newest", "Middle", "Oldest"]
    assert [r.name for r in read_journal(journal, follow=True).rows] == [
        "Oldest",
        "Middle",
        "Newest",
    ]


def test_a_tail_far_behind_catches_up_in_order_rather_than_skipping(tmp_path: Path) -> None:
    """Bounded like the page, and truncated at the *new* end — a viewer that attached late
    must not lose the rows between its position and the limit."""
    journal = write(tmp_path / "j.jsonl", *(row(name=f"P{n}") for n in range(250)))

    first = read_journal(journal, follow=True, since=0, limit=100)
    second = read_journal(journal, follow=True, since=first.next_index, limit=100)

    assert [r.name for r in first.rows][:2] == ["P0", "P1"]
    assert len(first.rows) == 100
    assert first.next_index == 100
    assert second.rows[0].name == "P100"


def test_a_position_past_the_end_is_clamped_rather_than_stranded(tmp_path: Path) -> None:
    """`lines_since`'s rule, for the same reason: a position the file cannot reach is a
    client that never sees another row, and it is silent."""
    journal = write(tmp_path / "j.jsonl", row(name="A"), row(name="B"), row(name="C"))

    page = read_journal(journal, follow=True, since=1000)

    assert page.rows == []
    assert page.next_index == 3


def test_a_torn_tail_is_re_read_rather_than_stepped_over(tmp_path: Path) -> None:
    """The position is the last row *parsed*, never the file's line count. A line caught
    mid-flush parses on the next poll; a position past it would drop that cycle for good."""
    journal = tmp_path / "j.jsonl"
    journal.write_text(row(name="A") + "\n" + '{"name": "To', encoding="utf-8")

    first = read_journal(journal, follow=True, since=0)
    write(journal, row(name="A"), row(name="B"))
    second = read_journal(journal, follow=True, since=first.next_index)

    assert [r.name for r in first.rows] == ["A"]
    assert (first.next_index, first.skipped) == (1, 1)
    assert [r.name for r in second.rows] == ["B"]


def test_a_page_reports_no_tail_position_because_a_page_does_not_tail(tmp_path: Path) -> None:
    """One field, one meaning. `next_index` is where to resume a *follow* from, and a
    paging client that sent it back as `since` would be told a different thing each call."""
    journal = write(tmp_path / "j.jsonl", *(row(name=f"P{n}") for n in range(10)))

    assert read_journal(journal, offset=0, limit=3).next_index == 0


def test_the_endpoint_tails_when_follow_is_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FANTABOT_DATA_DIR", str(tmp_path))
    write(tmp_path / "room_journal.jsonl", row(name="A"), row(name="B"))

    with TestClient(app) as client:
        body = client.get("/api/v1/asta/journal", params={"follow": 1, "since": 1}).json()

    assert [r["name"] for r in body["rows"]] == ["B"]
    assert body["next_index"] == 2

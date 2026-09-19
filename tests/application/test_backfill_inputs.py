"""What a backfill may be asked to load — the decisions both surfaces need (T22).

`aste_backfill`'s Typer body held all of them: which formats exist, which files must be
present, and what a missing listone costs. The app cannot reach a decision inside a
command body, so a picker built against the home would have invented its own idea of a
valid input — and a picker whose idea of "loadable" differs from the loader's offers a
file the child then exits 2 on.

**The enumeration is the new half and the reason the lift is worth making.** Nothing
anywhere decided what a *candidate* is. The home holds `live.jsonl` beside
`events_2026-08-26.jsonl`, three seeds, two `.offset` sidecars and one
`assignments_2026-08-26.jsonl` that is not a collector log at all — same extension, same
`auction_id` key, no `state`. A glob would offer all of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _paths import ONE_AUCTION

from fantabot.application.harvest_backfill import (
    InvalidBackfill,
    candidates,
    clean_backfill,
)


def _seed(path: Path) -> Path:
    auction_id = json.loads(ONE_AUCTION.read_text().splitlines()[0])["auction_id"]
    path.write_text(
        json.dumps([[auction_id, "15", 10, 500, 25, 25, "random", "free", 7, 7, "FIXTURE"]])
    )
    return path


# -- clean_backfill: the refusals, which both surfaces must share ---------------------


def test_an_unknown_format_is_refused_before_any_file_is_read(tmp_path: Path) -> None:
    """`asta_type` is NOT NULL and only two values are real, so a typo caught here beats
    a constraint violation after building 144,518 rows — and the events file is never
    even opened, which is what "before any work" means."""
    with pytest.raises(InvalidBackfill, match="manta"):
        clean_backfill(
            events=tmp_path / "never_read.jsonl",
            seed=_seed(tmp_path / "seed.json"),
            listone=tmp_path / "listone_map.json",
            asta_type="manta",
        )


def test_a_missing_events_file_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(InvalidBackfill, match=r"absent\.jsonl"):
        clean_backfill(
            events=tmp_path / "absent.jsonl",
            seed=_seed(tmp_path / "seed.json"),
            listone=tmp_path / "listone_map.json",
            asta_type="mantra",
        )


def test_a_missing_seed_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(InvalidBackfill, match=r"absent\.json"):
        clean_backfill(
            events=ONE_AUCTION,
            seed=tmp_path / "absent.json",
            listone=tmp_path / "listone_map.json",
            asta_type="mantra",
        )


def test_a_missing_listone_is_not_a_refusal_but_is_reported(tmp_path: Path) -> None:
    """An absent bridge costs every assignment its player link and no rows at all. That
    is a warning the operator must see and never a reason to refuse an evening's prices."""
    inputs = clean_backfill(
        events=ONE_AUCTION,
        seed=_seed(tmp_path / "seed.json"),
        listone=tmp_path / "absent.json",
        asta_type="mantra",
    )
    assert inputs.listone_present is False


# -- candidates: what the picker offers, and what it must not -------------------------


def _collector_log(path: Path, *, lines: int = 1) -> Path:
    path.write_text(
        "".join(
            json.dumps({"seen_at": "2026-08-26T18:21:05+00:00", "auction_id": "a", "state": {}})
            + "\n"
            for _ in range(lines)
        )
    )
    return path


def test_the_picker_offers_collector_logs_and_seeds_apart(tmp_path: Path) -> None:
    _collector_log(tmp_path / "live.jsonl")
    _collector_log(tmp_path / "events_2026-08-26.jsonl")
    _seed(tmp_path / "seed.json")
    _seed(tmp_path / "seed_2026-08-26.json")

    found = candidates(tmp_path)

    assert [log.name for log in found.logs] == [
        "events_2026-08-26.jsonl",
        "live.jsonl",
    ], "logs are listed by name, so the same home renders the same order twice"
    assert [seed.name for seed in found.seeds] == ["seed.json", "seed_2026-08-26.json"]
    assert [seed.rows for seed in found.seeds] == [1, 1], (
        "a seed's row count is how the operator tells today's 1,705-auction seed from a "
        "recorded evening's, and a mismatched pair is the silent failure mode"
    )


def test_the_live_landing_zone_is_offered_and_flagged(tmp_path: Path) -> None:
    """Hiding it would make the one file with 1.3 GB in it the one file the app cannot
    reach. A backfill of it reads only and upserts, so it cannot disturb the offset —
    which belongs to `harvest load`. The flag is so the operator knows which it is."""
    _collector_log(tmp_path / "live.jsonl")
    _collector_log(tmp_path / "events_2026-08-26.jsonl")

    by_name = {log.name: log for log in candidates(tmp_path).logs}

    assert by_name["live.jsonl"].live is True
    assert by_name["events_2026-08-26.jsonl"].live is False


def test_a_jsonl_that_is_not_a_collector_log_is_not_offered(tmp_path: Path) -> None:
    """`assignments_2026-08-26.jsonl` is a real file in the real home. It has the
    extension and it has `auction_id`; it has no `state`, so `read_jsonl` would hand
    `build` 18 records it drops as malformed and report a successful run that loaded
    nothing."""
    _collector_log(tmp_path / "live.jsonl")
    (tmp_path / "assignments_2026-08-26.jsonl").write_text(
        json.dumps({"auction_id": "a", "price": 903, "player_id": "x"}) + "\n"
    )

    assert [log.name for log in candidates(tmp_path).logs] == ["live.jsonl"]


def test_a_jsonl_of_untimed_records_is_not_offered(tmp_path: Path) -> None:
    """The `seen_at` half of the sniff. A record carrying a state and no timestamp is
    what `DroppedEvents.bad_timestamp` counts — `_parse_seen_at` returns `None` for a
    missing key — so a file of them is a collector log that loads nothing. Both
    near-misses in the real home are missing *both* keys, which is why neither exercises
    this half on its own."""
    _collector_log(tmp_path / "live.jsonl")
    (tmp_path / "untimed.jsonl").write_text(
        json.dumps({"auction_id": "a", "state": {"asta_state": "closed"}}) + "\n"
    )

    assert [log.name for log in candidates(tmp_path).logs] == ["live.jsonl"]


def test_a_jsonl_of_stateless_records_is_not_offered(tmp_path: Path) -> None:
    """The other half of the sniff. A record with a timestamp and no `state` is what
    `DroppedEvents.malformed_state` counts — a file of them reads as a collector log,
    loads nothing, and reports success. `assignments_2026-08-26.jsonl` misses the other
    key, so without this the `state` test is unexercised and can be deleted silently."""
    _collector_log(tmp_path / "live.jsonl")
    (tmp_path / "stateless.jsonl").write_text(
        json.dumps({"seen_at": "2026-08-26T18:21:05+00:00", "auction_id": "a"}) + "\n"
    )

    assert [log.name for log in candidates(tmp_path).logs] == ["live.jsonl"]


def test_the_sidecars_are_not_offered(tmp_path: Path) -> None:
    """`live.jsonl.offset`, `.state` and the two `.lock` files are a position in a file
    and a holder of it, not files a backfill can read. Each ends in its own suffix, so
    the `.jsonl`/`.json` test is the only guard needed — a second list of sidecar names
    beside it was a test that could not fail, and is gone."""
    _collector_log(tmp_path / "live.jsonl")
    (tmp_path / "live.jsonl.offset").write_text("123456")
    (tmp_path / "live.jsonl.state").write_text("{}")
    (tmp_path / "live.jsonl.loader.lock").write_text("")

    assert [log.name for log in candidates(tmp_path).logs] == ["live.jsonl"]


def test_an_empty_log_is_not_offered(tmp_path: Path) -> None:
    """A zero-byte `live.jsonl` is what a collect that never connected leaves. Offering
    it invites a run whose honest report — nothing built — reads as a broken backfill."""
    (tmp_path / "live.jsonl").write_text("")
    _collector_log(tmp_path / "events.jsonl")

    assert [log.name for log in candidates(tmp_path).logs] == ["events.jsonl"]


def test_each_candidate_carries_its_size_and_mtime(tmp_path: Path) -> None:
    """The home holds two files differing by three orders of magnitude and a picker
    showing two names alone gives the operator no way to tell yesterday's evening from
    a 300 KB fragment."""
    log = _collector_log(tmp_path / "events.jsonl", lines=4)

    only = candidates(tmp_path).logs[0]

    assert only.bytes == log.stat().st_size
    assert only.mtime is not None and only.mtime.endswith("+00:00")


def test_a_home_that_does_not_exist_is_an_empty_picker_not_a_crash(tmp_path: Path) -> None:
    """`harvest_dir()` names a directory nobody has created yet on a fresh install, and
    a status read that raises reports nothing about the one thing it is for."""
    found = candidates(tmp_path / "never_made")

    assert found.logs == () and found.seeds == ()
    assert found.exists is False
    assert found.error is None, "no home names a command; it is not a failure to report"


def test_a_file_where_the_home_should_be_is_not_a_home(tmp_path: Path) -> None:
    """`FANTABOT_HARVEST_DIR` is an exported variable a human types, and a path naming a
    file is the typo it makes. `iterdir` on one raises `NotADirectoryError`."""
    (tmp_path / "not_a_dir").write_text("")

    assert candidates(tmp_path / "not_a_dir").exists is False


def test_a_home_that_cannot_be_read_says_so_rather_than_reading_as_empty(
    tmp_path: Path,
) -> None:
    """The distinction the `is_dir` guard exists to make. An unreadable home and an
    absent one are both zero candidates, and they send the operator to two different
    places — a permissions dialog against `harvest adopt` — so they are two answers."""
    home = tmp_path / "home"
    home.mkdir()
    _collector_log(home / "live.jsonl")
    home.chmod(0o000)
    try:
        found = candidates(home)
    finally:
        home.chmod(0o700)

    assert found.exists is True, "the home is there; it is the listing that failed"
    assert found.error == "PermissionError"
    assert found.logs == ()

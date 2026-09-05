"""`fantabot-app harvest adopt` — moving the harvest artefacts into the home.

The landing zone, its byte offset and its reducer state describe **one position in one
file**. Moved apart, the loader either re-reads 1.31 GB (offset lost) or rebuilds a ladder
from nothing (state lost) — the two failures the 2026-09-05 Classic recovery was made of.
So this command moves them together or it moves nothing at all.

"Or nothing at all" is why it copies first and only then removes: a run that dies halfway
leaves every source file intact, and the next run — which is idempotent — finishes the job.
The destination is a different volume from the repository here, so a rename is not
available and the copy is real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantabot_app import harvest_home
from fantabot_app.cli import app

runner = CliRunner()


def _landing(root: Path, *, size: int = 4096, offset: int = 1024) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    live = root / "live.jsonl"
    live.write_bytes(b"x" * size)
    (root / "live.jsonl.offset").write_text(str(offset))
    (root / "live.jsonl.state").write_text('{"ladders": {}}')
    return live


def _plenty(_path: Path) -> int:
    return 1 << 40


class TestAdopt:
    def test_it_moves_the_landing_zone_with_its_offset_and_its_state(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source)

        report = harvest_home.adopt(source, destination, disk_free=_plenty)

        assert sorted(p.name for p in destination.iterdir()) == [
            "live.jsonl",
            "live.jsonl.offset",
            "live.jsonl.state",
        ]
        assert (destination / "live.jsonl.offset").read_text() == "1024"
        assert list(source.iterdir()) == []
        assert report.moved == 3

    def test_it_moves_every_artefact_not_only_the_named_three(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source)
        (source / "seed.json").write_text("[]")
        (source / "listone_map.json").write_text("{}")

        harvest_home.adopt(source, destination, disk_free=_plenty)

        assert (destination / "seed.json").exists()
        assert (destination / "listone_map.json").exists()

    def test_it_refuses_when_the_destination_cannot_hold_the_move(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source, size=4096)

        with pytest.raises(harvest_home.AdoptRefused) as refusal:
            harvest_home.adopt(source, destination, disk_free=lambda _p: 100)

        assert "100" in str(refusal.value)
        assert list(source.iterdir()) != [], "the source must survive a refusal untouched"
        assert not destination.exists()

    def test_the_free_space_refusal_names_an_absolute_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It offers `FANTABOT_HARVEST_DIR=<source>` as the way out, and a relative path
        there would rebuild the defect this whole task removes: a harvest home that only
        resolves from one directory."""
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source, size=4096)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(harvest_home.AdoptRefused) as refusal:
            harvest_home.adopt(Path("from"), destination, disk_free=lambda _p: 1)

        message = str(refusal.value)
        assert "FANTABOT_HARVEST_DIR=" in message
        offered = message.split("FANTABOT_HARVEST_DIR=")[1].split()[0]
        assert Path(offered).is_absolute(), offered

    def test_it_refuses_an_offset_whose_file_is_not_coming_with_it(self, tmp_path: Path) -> None:
        """A sidecar without its data file is a position in a file that is not here."""
        source, destination = tmp_path / "from", tmp_path / "to"
        source.mkdir(parents=True)
        (source / "orphan.jsonl.offset").write_text("512")

        with pytest.raises(harvest_home.AdoptRefused) as refusal:
            harvest_home.adopt(source, destination, disk_free=_plenty)

        assert "orphan.jsonl" in str(refusal.value)

    def test_it_refuses_an_offset_past_the_end_of_its_own_file(self, tmp_path: Path) -> None:
        """Past the end means the two do not describe the same file, and loading from it
        would silently skip everything after the real end."""
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source, size=4096, offset=9999)

        with pytest.raises(harvest_home.AdoptRefused) as refusal:
            harvest_home.adopt(source, destination, disk_free=_plenty)

        assert "9999" in str(refusal.value)
        assert (source / "live.jsonl").exists()

    def test_it_refuses_to_overwrite_a_different_file_already_there(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source, size=4096)
        destination.mkdir(parents=True)
        (destination / "live.jsonl").write_bytes(b"y" * 999)

        with pytest.raises(harvest_home.AdoptRefused) as refusal:
            harvest_home.adopt(source, destination, disk_free=_plenty)

        assert "live.jsonl" in str(refusal.value)
        assert (destination / "live.jsonl").read_bytes() == b"y" * 999

    def test_a_second_run_moves_nothing_and_says_so(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source)

        harvest_home.adopt(source, destination, disk_free=_plenty)
        again = harvest_home.adopt(source, destination, disk_free=_plenty)

        assert again.moved == 0
        assert (destination / "live.jsonl.offset").read_text() == "1024"

    def test_a_source_that_is_not_there_is_nothing_to_adopt(self, tmp_path: Path) -> None:
        report = harvest_home.adopt(tmp_path / "gone", tmp_path / "to", disk_free=_plenty)

        assert report.moved == 0

    def test_a_file_already_adopted_is_not_copied_twice(self, tmp_path: Path) -> None:
        """Identical name and identical size: the previous run's own work, not a clash."""
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source, size=4096)
        destination.mkdir(parents=True)
        (destination / "live.jsonl.state").write_text('{"ladders": {}}')

        report = harvest_home.adopt(source, destination, disk_free=_plenty)

        assert report.moved == 2
        assert report.skipped == 1


class TestCommand:
    def test_it_is_registered_under_the_harvest_group(self) -> None:
        result = runner.invoke(app, ["harvest", "adopt", "--help"])

        assert result.exit_code == 0
        assert "--from" in result.output

    def test_it_reports_the_refusal_and_exits_non_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "from"
        _landing(source, size=4096, offset=9999)
        monkeypatch.setattr(harvest_home, "harvest_dir", lambda: tmp_path / "to")

        result = runner.invoke(app, ["harvest", "adopt", "--from", str(source)])

        assert result.exit_code == 1
        assert "9999" in result.output

    def test_it_names_the_home_it_moved_into(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source)
        monkeypatch.setattr(harvest_home, "harvest_dir", lambda: destination)

        result = runner.invoke(app, ["harvest", "adopt", "--from", str(source)])

        assert result.exit_code == 0
        assert str(destination) in result.output
        assert (destination / "live.jsonl").exists()

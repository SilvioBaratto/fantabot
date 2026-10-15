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

import os
from pathlib import Path

import pytest
from fantabot.adapters.files.lock import COLLECTOR, LOADER, ROLES, RoleBusy, lock_path, role_lock
from typer.testing import CliRunner

from fantabot_app import harvest_home
from fantabot_app.cli import app

runner = CliRunner()

# Every test here runs `adopt`, which holds two lock files open while it works. Windows
# refuses to delete a file with an open handle; without this the suite only learns that on
# `app-ci`'s Windows runner.
pytestmark = pytest.mark.usefixtures("windows_file_semantics")


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

    def test_a_source_that_is_already_the_home_is_left_alone(self, tmp_path: Path) -> None:
        """The configuration an operator lands in the moment they set
        `FANTABOT_HARVEST_DIR` to the directory the artefacts are already in — which is
        exactly what a full home volume pushes them to do.

        Every file is then "already at the destination at the same size", which the
        idempotency branch reads as the previous run's own work and finishes by unlinking
        the source. The source *is* the destination, so that deletes the landing zone: 1.31
        GB of auction recordings that cannot be collected twice, reported as `moved=0`
        with no error.
        """
        zone = tmp_path / "zone"
        _landing(zone)
        (zone / "seed.json").write_text("[]")

        report = harvest_home.adopt(zone, zone, disk_free=_plenty)

        assert report.moved == 0
        assert sorted(p.name for p in zone.iterdir()) == [
            "live.jsonl",
            "live.jsonl.offset",
            "live.jsonl.state",
            "seed.json",
        ]

    def test_the_same_directory_reached_by_a_different_spelling_is_still_the_same(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`./data/aste_live` and an absolute path to it are one directory. Comparing the
        two `Path` objects would say otherwise, and unlink the lot."""
        zone = tmp_path / "zone"
        _landing(zone)
        monkeypatch.chdir(tmp_path)

        report = harvest_home.adopt(Path("zone"), zone.resolve(), disk_free=_plenty)

        assert report.moved == 0
        assert (zone / "live.jsonl").exists()

    def test_a_symlinked_source_is_still_the_same_directory(self, tmp_path: Path) -> None:
        zone = tmp_path / "zone"
        _landing(zone)
        link = tmp_path / "link"
        link.symlink_to(zone)

        report = harvest_home.adopt(link, zone, disk_free=_plenty)

        assert report.moved == 0
        assert (zone / "live.jsonl").exists()

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


class TestInterruptedCopy:
    def test_a_copy_killed_midway_does_not_poison_the_next_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        _landing(source)
        real_copy2 = harvest_home.shutil.copy2

        def dies_midway(src: Path, dst: Path) -> None:
            Path(dst).write_bytes(Path(src).read_bytes()[:10])
            raise KeyboardInterrupt

        monkeypatch.setattr(harvest_home.shutil, "copy2", dies_midway)
        with pytest.raises(KeyboardInterrupt):
            harvest_home.adopt(source, destination, disk_free=_plenty)
        monkeypatch.setattr(harvest_home.shutil, "copy2", real_copy2)

        report = harvest_home.adopt(source, destination, disk_free=_plenty)

        assert report.moved == 3
        assert (destination / "live.jsonl").stat().st_size == 4096
        assert sorted(p.name for p in destination.iterdir()) == [
            "live.jsonl",
            "live.jsonl.offset",
            "live.jsonl.state",
        ]


class TestLiveSource:
    def test_it_refuses_while_a_collector_holds_the_source(self, tmp_path: Path) -> None:
        from fantabot.adapters.files.lock import COLLECTOR, role_lock

        source, destination = tmp_path / "from", tmp_path / "to"
        live = _landing(source)

        with (
            role_lock(live, COLLECTOR),
            pytest.raises(harvest_home.AdoptRefused, match="collector"),
        ):
            harvest_home.adopt(source, destination, disk_free=_plenty)

        assert live.exists()
        assert not destination.exists()

    def test_it_refuses_while_a_loader_holds_the_source(self, tmp_path: Path) -> None:
        from fantabot.adapters.files.lock import LOADER, role_lock

        source, destination = tmp_path / "from", tmp_path / "to"
        live = _landing(source)

        with (
            role_lock(live, LOADER),
            pytest.raises(harvest_home.AdoptRefused, match="loader"),
        ):
            harvest_home.adopt(source, destination, disk_free=_plenty)

        assert live.exists()


class TestTheRolesOutliveTheMove:
    """What `adopt` must not give up in order to run where a held file cannot be removed."""

    @staticmethod
    def _held_roles(live: Path) -> set[str]:
        """The roles that cannot be taken right now, found by trying to take them."""
        held = set()
        for role in ROLES:
            try:
                with role_lock(live, role):
                    pass
            except RoleBusy:
                held.add(role)
        return held

    def test_both_roles_are_held_at_every_step_that_touches_the_landing_zone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A collector appends through its open handle to a file that is then unlinked, and
        a loader moves `.offset` under the copy: either loses records without an error. So
        the roles must be held from the free-space check to the last source file going, and
        releasing them early to get the lock files deleted would be the cheap way to make
        Windows pass."""
        source, destination = tmp_path / "from", tmp_path / "to"
        live = _landing(source)
        seen: list[tuple[str, set[str]]] = []

        def sample(step: str) -> None:
            seen.append((step, self._held_roles(live)))

        def disk_free(path: Path) -> int:
            sample("free-space check")
            return _plenty(path)

        real_copy2, real_unlink = harvest_home.shutil.copy2, os.unlink

        def copying(src: Path, dst: Path) -> object:
            sample(f"copy {Path(src).name}")
            return real_copy2(src, dst)

        def unlinking(path: Path, *args: object, **kwargs: object) -> None:
            if Path(path).parent == source and not Path(path).name.endswith(".lock"):
                sample(f"unlink {Path(path).name}")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(harvest_home.shutil, "copy2", copying)
        monkeypatch.setattr(os, "unlink", unlinking)

        harvest_home.adopt(source, destination, disk_free=disk_free)

        steps = [step for step, _ in seen]
        assert steps == [
            "free-space check",
            "copy live.jsonl",
            "copy live.jsonl.offset",
            "copy live.jsonl.state",
            "unlink live.jsonl",
            "unlink live.jsonl.offset",
            "unlink live.jsonl.state",
        ]
        assert [held for _, held in seen] == [set(ROLES)] * len(seen)

    def test_the_lock_files_go_once_the_roles_are_released(self, tmp_path: Path) -> None:
        source, destination = tmp_path / "from", tmp_path / "to"
        live = _landing(source)

        harvest_home.adopt(source, destination, disk_free=_plenty)

        assert list(source.iterdir()) == []
        assert self._held_roles(live) == set()

    def test_lock_files_are_left_where_nothing_was_moved(self, tmp_path: Path) -> None:
        """A directory with no landing zone in it is not made tidier by a command that
        reports it moved nothing."""
        source, destination = tmp_path / "from", tmp_path / "to"
        source.mkdir()
        stale = lock_path(source / "live.jsonl", COLLECTOR)
        stale.write_bytes(b"")

        report = harvest_home.adopt(source, destination, disk_free=_plenty)

        assert report.moved == 0
        assert stale.exists()

    def test_a_lock_someone_holds_by_then_is_left_in_place(self, tmp_path: Path) -> None:
        """The roles are released before the lock files are deleted, so a collector started
        in the emptied directory in between holds one. Its lock must stay, and the move that
        has already happened must not be reported as a failure."""
        live = tmp_path / "live.jsonl"
        idle = lock_path(live, LOADER)
        idle.write_bytes(b"")

        with role_lock(live, COLLECTOR) as held:
            harvest_home._discard_locks([held, idle])

        assert held.exists()
        assert not idle.exists()

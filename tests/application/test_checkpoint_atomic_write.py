"""A follower killed while writing the offset must leave the old offset, not an empty file."""

from __future__ import annotations

from pathlib import Path

import pytest

from fantabot.application.harvest_loader import Checkpoint


def test_a_kill_before_the_move_leaves_the_previous_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    landing = tmp_path / "live.jsonl"
    checkpoint = Checkpoint(landing)
    checkpoint.write(5)

    def killed(self: Path, target: object) -> Path:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", killed)
    with pytest.raises(KeyboardInterrupt):
        checkpoint.write(9)
    monkeypatch.undo()

    assert checkpoint.read() == 5


def test_a_write_round_trips(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path / "live.jsonl")
    checkpoint.write(42)
    assert checkpoint.read() == 42

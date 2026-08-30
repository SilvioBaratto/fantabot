"""JSON output for the collected grid. The only module here that touches disk."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from fantabot.resources import COMPAT_FILENAME, SCHEMI_FILENAME

__all__ = ["COMPAT_FILENAME", "SCHEMI_FILENAME", "write_json"]


def write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

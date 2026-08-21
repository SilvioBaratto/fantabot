"""JSON output for the collected grid. The only module here that touches disk."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

SCHEMI_FILENAME = "mantra_schemi.json"
COMPAT_FILENAME = "mantra_compat.json"


def write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

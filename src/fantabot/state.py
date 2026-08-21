import json
from pathlib import Path
from typing import Any

from fantabot.config import settings

_DEFAULT_STATE: dict[str, Any] = {
    "last_lineup_matchday": None,
    "last_auction_session_id": None,
    "processed_bids": [],
}


def load() -> dict[str, Any]:
    path = settings.fantabot_state_file
    if not path.exists():
        return dict(_DEFAULT_STATE)
    return {**_DEFAULT_STATE, **json.loads(path.read_text())}


def save(state: dict[str, Any]) -> None:
    path = settings.fantabot_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str))


def storage_state_path() -> Path:
    return settings.fantabot_storage_state

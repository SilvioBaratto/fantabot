"""Mantra modules — the 11 schemi and their per-slot role composition.

Static package data (mantra_schemi.json via fantabot's resources), the grid L1 matches a
roster against. No DB, no network. Degrades open (empty grid if the artefact is missing).
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Schema(BaseModel):
    nome: str
    slots: list[list[str]]


class LegalityGrid(BaseModel):
    schemi: list[Schema]
    roles: list[str]


def load_schemi() -> list[Schema]:
    from fantabot.domain.shared import resources

    path = resources.data_dir() / resources.SCHEMI_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Schema(nome=entry["nome"], slots=entry["slots"]) for entry in data.get("schemi", [])]


@router.get("/asta/legality", response_model=LegalityGrid, tags=["asta"])
def legality() -> LegalityGrid:
    try:
        schemi = load_schemi()
        roles = sorted({role for schema in schemi for slot in schema.slots for role in slot})
        return LegalityGrid(schemi=schemi, roles=roles)
    except Exception:  # noqa: BLE001 — degrade open
        return LegalityGrid(schemi=[], roles=[])

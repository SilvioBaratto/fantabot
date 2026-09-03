"""F3 — the API reuses fantabot's single engine; there is no second engine here.

This is the module-level guard behind SPEC A6 ("one DB, one engine"): the api database
module must expose only ``get_db``, delegating to fantabot's ``database_manager`` — no
private ``create_engine``/``sessionmaker``.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest


def test_api_database_module_has_no_second_engine() -> None:
    from app.infrastructure import database

    assert not hasattr(database, "engine")
    assert not hasattr(database, "SessionLocal")


def test_get_db_yields_a_session_from_fantabot_database_manager(monkeypatch) -> None:
    from app.infrastructure import database

    sentinel = object()

    @contextmanager
    def fake_get_session():
        yield sentinel

    monkeypatch.setattr(database.database_manager, "get_session", fake_get_session)

    gen = database.get_db()
    assert next(gen) is sentinel
    with pytest.raises(StopIteration):
        next(gen)

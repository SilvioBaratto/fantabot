"""S9 — /asta/plan (RosterRules injection + degrade)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from fantabot_app.api.main import app


def test_the_route_reads_the_band_through_the_shared_reader() -> None:
    """`build_roster_rules` lived here and is gone (2.1).

    Its three tests went with it, and that is a move rather than a loss: the behaviour they
    covered — a band from the snapshot, and a fallback when fields are missing — is now
    `domain/asta/state.rules_for_lega`, tested in `tests/domain/asta/test_asta_rules_for_lega.py`
    with ten cases instead of three, including the Classic half this route never had and the
    provenance constant it never returned.

    What is left to assert *here* is the wiring: the route must not grow a second reader.
    """
    from fantabot_app.api.v1.endpoints import asta as endpoint

    assert not hasattr(endpoint, "build_roster_rules"), (
        "the endpoint grew its own roster-rules reader again — that is the duplication 2.1 "
        "removed; call `application.lega_reads.rules_for_league`"
    )

    source = Path(endpoint.__file__).read_text(encoding="utf-8")
    assert "rules_for_league(" in source, "the route no longer reads the lega's band at all"


def test_asta_plan_degrades_open_on_db_error(monkeypatch) -> None:
    from fantabot.adapters.persistence import database_manager

    def boom():
        # A real driver failure, not a bare RuntimeError: since 1.7 the route names
        # the families it catches, so an induced failure has to be one of them.
        raise OperationalError("SELECT 1", {}, OSError("db unreachable"))

    monkeypatch.setattr(database_manager, "get_session", boom)

    response = TestClient(app).get("/api/v1/asta/plan?league_id=4103937")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["players"] == []

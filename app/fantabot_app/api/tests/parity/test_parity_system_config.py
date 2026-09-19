"""`config-check` and `GET /system/config`, over one report.

This is the tier's smallest comparison and the one with the sharpest motive. The screen
exists to answer *which database am I on?* — and the reason it exists is that the CLI and
the app once answered differently: `.env` pointing `fantabot` at a compose Postgres while
`fantabot-app` had provisioned its own is what let a week of Classic auction collection
read as lost. A screen that can disagree with the command about that is worse than no
screen, because it is believed.

There is one implementation — both sides call `application/config_report.build_report` —
so what can diverge is the wiring: a field dropped in the endpoint's mapping, a mask
applied on one surface and not the other, or the two reading different settings objects.
The last is not hypothetical either: `settings` is a module singleton, and the parity
tier exists at all because `database_manager` built its factory from it at import.

**Compared against the shared call, not against each other's text.** The tier's rule is
*compare decision content, never rendered text*; the CLI's output is Rich's, and asserting
on it would pin formatting. So the endpoint is compared field-for-field with
`build_report()`, and the command is asserted to carry the same DSN — which is the one
string an operator actually reads off either surface and acts on.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from typer.testing import Result

#: Distinctive on every axis an operator would check — host, port, database — so "both
#: surfaces read the same settings object" is a claim these assertions can refute rather
#: than one they satisfy by both reading the same default.
DISTINCT_DSN = "postgresql+psycopg2://bot:ParityCanary9@db.parity.test:65432/parity_db"


@pytest.fixture
def distinct_dsn(monkeypatch: pytest.MonkeyPatch) -> str:
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_database_url", DISTINCT_DSN)
    return DISTINCT_DSN


def test_the_page_and_the_command_name_the_same_database(
    api: TestClient, cli: Callable[..., Result], distinct_dsn: str
) -> None:
    """The incident, as an assertion.

    `asta optimize` and `GET /asta/plan` differed in ten inputs because nothing compared
    them; this is the same test one screen along, and it is cheap because the answer is a
    single string.
    """
    from fantabot.application.config_report import build_report

    expected = build_report().database_url
    body = api.get("/api/v1/system/config").json()
    output: Result = cli("config-check")

    assert body["database_url"] == expected
    assert expected in output.output, (
        "the command's DSN line is not the report's:\n" + output.output
    )
    # The password is masked on both, and the masking is what makes the two strings
    # comparable at all — an unmasked surface would differ here for the right reason and
    # be caught for the wrong one.
    assert "ParityCanary9" not in body["database_url"]
    assert "ParityCanary9" not in output.output
    assert "db.parity.test:65432" in body["database_url"]
    assert "parity_db" in body["database_url"]


def test_the_endpoint_sends_every_field_the_report_holds(api: TestClient) -> None:
    """A field the mapping drops is invisible from the page and present in the command —
    which is exactly the divergence this tier is for, and the cheapest kind to introduce."""
    from fantabot.application.config_report import build_report

    report = build_report()
    body = api.get("/api/v1/system/config").json()

    assert body["settings"] == dict(report.settings)
    assert body["secrets_set"] == dict(report.secrets_set)
    assert body["database_url"] == report.database_url
    assert body["database_url_error"] == report.database_url_error


def test_both_surfaces_report_the_same_secrets_as_set(
    api: TestClient, cli: Callable[..., Result], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Masked is not the same as invisible, on either surface.

    A canary is set first because a presence check passes vacuously on a machine with
    nothing configured — which is every CI machine, and the one place this matters least
    to catch.
    """
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "ParityKeyCanary")

    body = api.get("/api/v1/system/config").json()
    output: Result = cli("config-check")

    assert body["secrets_set"]["fantabot_encryption_key"] is True
    assert "fantabot_encryption_key set: True" in output.output
    assert "ParityKeyCanary" not in output.output
    assert "ParityKeyCanary" not in api.get("/api/v1/system/config").text

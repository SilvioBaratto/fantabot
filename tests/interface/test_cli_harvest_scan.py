"""`fantabot harvest scan`, the Typer body. **Zero sockets.**

Measured 2026-09-24 at **0 of 33 body statements** — the largest of the five commands no
test entered at all, and the one where that mattered most: it is the command that decides
*which auctions get collected all evening*, and every one of the following is a decision
made here and nowhere else.

* **Both formats are fetched; `--only` filters afterwards.** Filtering at collection time
  is what threw away 85% of the population once already, so the order of those two
  statements is the fix, and a test that only counts the output cannot see it.
* **An empty scan and an expired session are refusals, not results.** Reporting `live 0`
  for either would look exactly like a quiet night, and the next scan would never be run.
  They exit 1; a missing session exits 2, because that one has an operator action.
* **The registry is merged, never replaced.** An auction that has dropped off the live
  list is still in the seed — losing it means losing an evening already being collected.
* **The seed defaults to the harvest home**, resolved when the command runs. Naming one
  explicitly is how a second landing zone with its own checkpoint gets created.
* **A bearer never reaches a local here.** The client is built from the store inside the
  session, which is the property `from_store` exists for.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.domain.harvest.registry import AuctionConfig
from fantabot.interface.app import app

runner = CliRunner()


class _Session:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _config(auction_id: str, asta_type: str = "mantra", **kw: Any) -> AuctionConfig:
    return AuctionConfig(
        auction_id=auction_id,
        db_shard=kw.pop("db_shard", "1"),
        asta_type=asta_type,
        name=kw.pop("name", f"asta {auction_id}"),
        num_teams=kw.pop("num_teams", 8),
        num_credits=kw.pop("num_credits", 500),
        **kw,
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """A usable key, a session that does not touch a database, and a fake scan."""
    from fantabot import config
    from fantabot.adapters.http.harvest import client as client_module
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings, "fantabot_encryption_key", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(config, "harvest_dir", lambda: tmp_path)

    state = SimpleNamespace(scanned=[_config("a")], raises=None, from_store_raises=None)

    class _Client:
        def live_auctions(self) -> list[AuctionConfig]:
            if state.raises is not None:
                raise state.raises
            return list(state.scanned)

    def _from_store(_store: Any, *_a: Any, **_kw: Any) -> _Client:
        if state.from_store_raises is not None:
            raise state.from_store_raises
        return _Client()

    monkeypatch.setattr(client_module.LiveAuctionsClient, "from_store", _from_store)
    return SimpleNamespace(state=state, home=tmp_path, seed=tmp_path / "seed.json")


def _rows(seed: Path) -> list[list[Any]]:
    data: list[list[Any]] = json.loads(seed.read_text(encoding="utf-8"))
    return data


def test_a_missing_session_exits_2_and_writes_no_seed(wired: SimpleNamespace) -> None:
    """Exit 2, not 1: the operator has something to do — `auth fantalab-login`."""
    from fantabot.domain.tokens.errors import FantalabSessionMissing

    # The message is the exception's own — it names the command that fixes it, and the
    # body must print that rather than composing a second sentence beside it.
    wired.state.from_store_raises = FantalabSessionMissing()

    result = runner.invoke(app, ["harvest", "scan"])

    assert result.exit_code == 2
    assert "auth fantalab-login" in result.output
    assert not wired.seed.exists()


@pytest.mark.parametrize("failure", ["AuthExpired", "ScanEmpty"])
def test_a_refused_scan_exits_1_rather_than_reporting_zero(
    wired: SimpleNamespace, failure: str
) -> None:
    """Both are refusals. `live 0` would read as a quiet night and the seed would be
    rewritten empty on top of a registry that is mid-collection."""
    from fantabot.adapters.http.harvest import client as client_module

    wired.seed.write_text(json.dumps([["kept", "1", 8, 500, 1, 30, "m", "r", 5, 9, "n"]]))
    wired.state.raises = getattr(client_module, failure)("refused")

    result = runner.invoke(app, ["harvest", "scan"])

    assert result.exit_code == 1
    assert "refused" in result.output
    assert _rows(wired.seed)[0][0] == "kept"


def test_a_first_scan_writes_the_seed_and_counts_both_formats(
    wired: SimpleNamespace,
) -> None:
    wired.state.scanned = [_config("a"), _config("b"), _config("c", "classic")]

    result = runner.invoke(app, ["harvest", "scan"])

    assert result.exit_code == 0
    assert "live 3 (classic 1, mantra 2)" in result.output
    assert "registry 0 -> 3 (+3)" in result.output
    assert {row[0] for row in _rows(wired.seed)} == {"a", "b", "c"}


def test_only_filters_the_output_but_not_the_fetch(wired: SimpleNamespace) -> None:
    """The counts line reports what survived the filter, and the registry holds only
    those — but the fetch was for both formats, which is the ordering that matters."""
    wired.state.scanned = [_config("a"), _config("c", "classic")]

    result = runner.invoke(app, ["harvest", "scan", "--only", "classic"])

    assert result.exit_code == 0
    assert "live 1 (classic 1)" in result.output
    assert [row[0] for row in _rows(wired.seed)] == ["c"]


def test_an_auction_no_longer_live_survives_the_merge(wired: SimpleNamespace) -> None:
    """The seed is a registry, not a snapshot of this instant. An evening already being
    collected drops off the live list long before it is finished."""
    wired.seed.write_text(
        json.dumps([["old", "1", 8, 500, 1, 30, "m", "r", 5, 9, "an older asta"]])
    )
    wired.state.scanned = [_config("new")]

    result = runner.invoke(app, ["harvest", "scan"])

    assert result.exit_code == 0
    assert "registry 1 -> 2 (+1)" in result.output
    assert {row[0] for row in _rows(wired.seed)} == {"old", "new"}


def test_a_legacy_seed_row_is_read_as_mantra(wired: SimpleNamespace) -> None:
    """The file predates storing the format, and everything written before it was Mantra.
    Reading those rows as anything else would mislabel a whole recorded corpus."""
    wired.seed.write_text(
        json.dumps([["old", "1", 8, 500, 1, 30, "m", "r", 5, 9, "an older asta"]])
    )
    wired.state.scanned = []

    runner.invoke(app, ["harvest", "scan"])

    by_id = {row[0]: row for row in _rows(wired.seed)}
    assert by_id["old"][-1] == "mantra"


def test_the_seed_defaults_to_the_harvest_home(wired: SimpleNamespace) -> None:
    """Resolved when the command runs, not at import. A default bound in the `def` line
    would hold the home of whichever process imported typer first."""
    result = runner.invoke(app, ["harvest", "scan"])

    assert result.exit_code == 0
    assert wired.seed.exists()
    assert wired.seed.parent == wired.home


def test_an_explicit_seed_is_created_where_it_is_named(
    wired: SimpleNamespace, tmp_path: Path
) -> None:
    """The documented footgun, pinned rather than fixed: naming a seed creates it, home
    or not. What must hold is that the home's own seed is then left alone."""
    elsewhere = tmp_path / "elsewhere" / "seed.json"

    result = runner.invoke(app, ["harvest", "scan", "--seed", str(elsewhere)])

    assert result.exit_code == 0
    assert elsewhere.exists()
    assert not wired.seed.exists()

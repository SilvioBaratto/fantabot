"""The walker sees the imports a layer rule is actually about.

A layer test is only as good as its graph. The three cases that matter here are the
three this repository actually had, and each of them is invisible to the obvious
implementation:

* an import inside a function body,
* an import under `if TYPE_CHECKING:`,
* a re-export shim, where the offending edge is one hop further on.

So the mechanics are tested against a synthetic tree, where the expected answer is
written down rather than measured, and then checked once against the real package to
confirm the synthetic tree is not a world of its own.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import _importgraph as G
import pytest


@pytest.fixture
def tree(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Point the walker at a package we wrote, and undo its caches.

    Both public functions are `lru_cache`d — correct in production, where the tree does
    not move under them, and a cross-test leak here.
    """

    def build(**modules: str) -> None:
        root = tmp_path / "pkg"
        root.mkdir(exist_ok=True)
        (root / "__init__.py").write_text("")
        for name, body in modules.items():
            path = root.joinpath(*name.split("."))
            path.parent.mkdir(parents=True, exist_ok=True)
            for parent in path.parents:
                if parent == root:
                    break
                (parent / "__init__.py").touch()
            path.with_suffix(".py").write_text(textwrap.dedent(body))

        monkeypatch.setattr(G, "SRC", tmp_path)
        monkeypatch.setattr(G, "PACKAGE", root)
        G.direct_imports.cache_clear()
        G.reachable.cache_clear()

    yield build
    G.direct_imports.cache_clear()
    G.reachable.cache_clear()


class TestTheImportsThatHide:
    """Each of these shipped in this repository while looking pure."""

    def test_an_import_inside_a_function_body_counts(self, tree) -> None:  # type: ignore[no-untyped-def]
        """`asta_engine/prices.py`, exactly: pure at module level, Postgres per call."""
        tree(**{"pure": "def go():\n    from sqlalchemy import select\n    return select"})
        assert G.reaches("pkg.pure", "sqlalchemy")

    def test_a_type_checking_import_counts(self, tree) -> None:  # type: ignore[no-untyped-def]
        """Not executed, but it is still a dependency of the module's *design*.

        A module whose signatures are written in terms of a Session belongs to the
        layer that owns Sessions, whether or not the import runs.
        """
        tree(**{"typed": """
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from sqlalchemy.orm import Session
        """})
        assert G.reaches("pkg.typed", "sqlalchemy")

    def test_a_re_export_shim_does_not_hide_what_is_behind_it(self, tree) -> None:  # type: ignore[no-untyped-def]
        """The repository has had two of these. A direct-import check reads them as leaves."""
        tree(
            **{
                "caller": "from pkg.shim import thing",
                "shim": "from pkg.heavy import thing as thing",
                "heavy": "import sqlalchemy",
            }
        )
        assert G.reaches("pkg.caller", "sqlalchemy")
        assert G.why("pkg.caller", "sqlalchemy") == [
            "pkg.caller",
            "pkg.shim",
            "pkg.heavy",
            "sqlalchemy",
        ]


class TestResolution:
    def test_a_relative_import_is_the_same_edge_as_an_absolute_one(self, tree) -> None:  # type: ignore[no-untyped-def]
        """This package is dense with `from ..x import y`; missing those is a hole."""
        tree(
            **{
                "sub.here": "from ..other import thing",
                "other": "import sqlalchemy",
            }
        )
        assert G.reaches("pkg.sub.here", "sqlalchemy")

    def test_a_package_import_is_prefix_matched(self, tree) -> None:  # type: ignore[no-untyped-def]
        """A rule names `fantabot.adapters.persistence`; the edge is to `fantabot.adapters.persistence.repositories.aste`."""
        tree(**{"caller": "from pkg.deep.inner import thing", "deep.inner": ""})
        assert G.reaches("pkg.caller", "pkg.deep")
        assert not G.reaches("pkg.caller", "pkg.deeper")

    def test_from_x_import_y_where_y_is_a_module(self, tree) -> None:  # type: ignore[no-untyped-def]
        """`from pkg import mod` names a module, not an attribute — the edge is real."""
        tree(**{"caller": "from pkg import leaf", "leaf": "import sqlalchemy"})
        assert G.reaches("pkg.caller", "sqlalchemy")

    def test_third_party_is_a_leaf_and_is_not_followed(self, tree) -> None:  # type: ignore[no-untyped-def]
        """Otherwise the walk would wander into site-packages and prove nothing."""
        tree(**{"caller": "import json"})
        assert G.reachable("pkg.caller") == frozenset({"pkg.caller", "json"})

    def test_a_cycle_terminates(self, tree) -> None:  # type: ignore[no-untyped-def]
        tree(**{"a": "from pkg import b", "b": "from pkg import a"})
        assert G.reaches("pkg.a", "pkg.b")
        assert G.reaches("pkg.b", "pkg.a")


def test_why_returns_an_empty_path_when_there_is_none(tree) -> None:  # type: ignore[no-untyped-def]
    tree(**{"a": "import json"})
    assert G.why("pkg.a", "sqlalchemy") == []


class TestAgainstTheRealTree:
    """The synthetic tests could all pass on a walker that cannot read this package."""

    def test_it_finds_every_module(self) -> None:
        found = set(G.modules())
        assert "fantabot.cli" in found
        assert "fantabot.asta_engine.optimizer" in found
        assert len(found) > 90

    def test_the_two_known_leaks_are_seen(self) -> None:
        """Both are function-body imports, and both are P11's reason for existing.

        If these ever come back False *without* the corresponding split having landed,
        the walker has stopped seeing function-level imports — which is the failure
        mode that would quietly disarm every rule in `test_layers.py`.
        """
        leaks = {
            m
            for m in ("fantabot.asta_engine.prices", "fantabot.news.pool")
            if G.reaches(m, "fantabot.adapters.persistence")
        }
        split = {
            m
            for m in ("fantabot.asta_engine.prices", "fantabot.news.pool")
            if "import" not in Path(
                G.SRC.joinpath(*m.split("."))
            ).with_suffix(".py").read_text().split("def ")[-1]
        }
        assert leaks or split, "neither module leaks and neither was split — check the walker"

    def test_it_does_not_import_what_it_reads(self) -> None:
        """The point of the AST walk. Importing would run `Settings()` and open sockets.

        The claim is about the *walk*, so the measurement is a delta. Asserting on
        `sys.modules` outright passed alone and failed in the suite, where the agentkit
        tests have legitimately imported the SDK long before this runs — a test that
        depends on execution order, measuring the process rather than the code.
        """
        before = set(sys.modules)
        G.reachable("fantabot.cli")  # reaches playwright, sqlalchemy and the agent SDK
        assert set(sys.modules) - before == set()

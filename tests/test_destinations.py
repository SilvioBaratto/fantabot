"""The W6 destination map is complete and does not contradict the layer table."""

from __future__ import annotations

import _importgraph as G
from _destinations import OVERRIDE, destination
from test_layers import UNPLACED, layer_of


def _modules() -> list[str]:
    return [m[len("fantabot."):] for m in G.modules() if m not in UNPLACED and m != "fantabot"]


def test_every_module_has_a_destination() -> None:
    """A module with no destination is one somebody moves by guess on the day."""
    assert _modules()
    for module in _modules():
        assert destination(module, layer_of(f"fantabot.{module}"))


def test_no_destination_contradicts_the_layer_it_is_filed_under() -> None:
    """The check that caught two of my own rows.

    A destination is a claim about a module's layer, written a second time. `live.py` and
    `sink.py` were both filed under `application/` while the table called them domain --
    and the table was right, since both are pure. Without this the tree would have said
    one thing and the enforced rule another.
    """
    offenders = [
        (module, layer, path)
        for module in _modules()
        for layer in [layer_of(f"fantabot.{module}")]
        for path in [destination(module, layer)]
        if path != "config.py" and not path.startswith(f"{layer}/")
    ]

    assert offenders == [], offenders


def test_no_two_modules_land_on_the_same_path() -> None:
    """A collision silently deletes one of them during the move."""
    seen: dict[str, str] = {}
    clashes = []
    for module in _modules():
        path = destination(module, layer_of(f"fantabot.{module}"))
        if path in seen:
            clashes.append((seen[path], module, path))
        seen[path] = module

    assert clashes == [], clashes


def test_every_override_names_a_module_that_exists() -> None:
    """An override for a module that was renamed or deleted moves nothing, silently."""
    known = set(_modules())
    assert sorted(set(OVERRIDE) - known) == []

"""Where each module goes in W6, and the check that the map agrees with itself.

The plan named three files no move task owned. Building the map found the real number is
larger, because P9-P11 added modules the plan predates -- `aste/incremental.py`,
`fantalab/listone.py`, `news/read.py`, `resources.py` -- and because two of the
destinations I wrote by hand contradicted the layer table: `asta_engine/live.py` and
`news/sink.py` were both filed under `application/` when both are pure. The table was
right. `sink.py` batches and flushes through an *injected* callable and `live.py`'s
docstring says outright that all four parsers are pure, which is why they are tested
without a socket.

That is the whole reason this is a checked table rather than a list in a document: a
destination is a claim about a module's layer, and a claim written twice drifts.
"""

from __future__ import annotations

#: Modules whose destination is not mechanical -- a rename, or a layer the package name
#: does not imply. Everything else is `<layer>/<feature>/<module>.py`.
OVERRIDE: dict[str, str] = {
    # application: the use cases, named for what they do rather than where they were
    "asta_engine.plan": "application/asta_planner.py",
    "aste.loader": "application/harvest_loader.py",
    "aste.supervisor": "application/harvest_supervisor.py",
    "news.pipeline": "application/news_fetcher.py",
    "mantra_grid.collect": "application/mantra_collector.py",
    "pricing": "application/pricing.py",
    "login": "application/auth_login.py",
    "fantalab_login": "application/fantalab_login.py",
    # adapters: one subpackage per kind of outside world
    "news.read": "adapters/persistence/news_pool.py",
    "data_sources.news_sentiment": "adapters/persistence/news_sentiment.py",
    "aste.client": "adapters/http/harvest/client.py",
    "aste.transport": "adapters/http/harvest/transport.py",
    "aste.stream": "adapters/http/harvest/stream.py",
    "apileague": "adapters/http/apileague.py",
    "aste.landing": "adapters/files/landing.py",
    "mantra_grid.writer": "adapters/files/mantra_writer.py",
    "browser": "adapters/browser/capture.py",
    "state": "adapters/browser/storage_state.py",
    # domain
    "data_sources.models": "domain/shared/values.py",
    "parsing": "domain/shared/parsing.py",
    "club_names": "domain/shared/club_names.py",
    "resources": "domain/shared/resources.py",
    # A package whose modules split across layers needs its `__init__.py` placed by
    # hand, as an entry here, and the entry is removed once the move is done -- a map
    # naming something that no longer exists moves nothing and says nothing.
    # `tokens/` was the first: its `__init__.py` went to `domain/tokens/` because it
    # re-exports from `errors` and `status` only, both domain, and its own docstring
    # records why `TokenStore` is deliberately not among them -- re-exporting the
    # shell from the pure half is a real import cycle.
    # interface
    "cli": "interface/app.py",
    "asta_engine.cli": "interface/asta.py",
    "aste.cli": "interface/harvest.py",
    "interface.console": "interface/console.py",
    "interface.options": "interface/options.py",
    # `config.py` is named in the target tree as the one module both sides may read.
    "config": "config.py",
}

#: Today's top-level package -> the feature directory it becomes inside its layer.
FEATURE: dict[str, str] = {
    "asta_engine": "asta",
    "aste": "harvest",
    "news": "news",
    "mantra_grid": "mantra",
    "db": "persistence",
    "agentkit": "agent",
    "fantalab": "http/fantalab",
    "tokens": "tokens",
    "scrapers": "scraping",
    "data_sources": "shared",
    "interface": "",
}


#: The four layer roots. A module already under one of them has arrived.
LAYERS = ("domain", "application", "adapters", "interface")


def destination(module: str, layer: str) -> str:
    """The path `module` moves to. `module` is dotted and package-relative.

    Idempotent: a module already inside a layer is its own destination. Without that,
    re-deriving the map after a move sends `domain.tokens` to `domain/shared/tokens.py`,
    because the feature table knows `tokens` and not `domain`.
    """
    if module.split(".")[0] in LAYERS:
        return module.replace(".", "/") + ".py"
    if module in OVERRIDE:
        return OVERRIDE[module]
    head, _, tail = module.partition(".")
    feature = FEATURE.get(head, "shared")
    tail = tail or head
    return f"{layer}/{feature}/{tail.replace('.', '/')}.py".replace("//", "/")

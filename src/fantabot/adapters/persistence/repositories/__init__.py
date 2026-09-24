"""Repositories: every query the application makes lives behind one of these.

**Nothing is re-exported here, deliberately.** This package had an eight-name ``__all__``
covering five of its eleven repository classes, and not one import in the tree went through
it — all 126, measured 2026-09-24, name the submodule (``repositories.aste``,
``repositories.league``). A partial re-export is worse than none: it reads as the package's
public face while six classes are missing from it, so the next repository is added to a list
nobody imports, or left out of one that looks authoritative.
"""

"""`CLAUDE.md` and `README.md` describe the tree that exists.

SC 27, checked as two things rather than read over: every file path they name is a file,
and every command they show answers `--help`. Documentation rots silently -- a path that
moved still reads correctly -- and this phase moved 94 modules, so a doc check that is a
human re-reading is a doc check that happens once.

Deleted things are named on purpose. Both files explain what was removed and why, so a
reference is only stale if it is *presented as current*; the ones below are in sentences
about their removal. They are listed rather than pattern-matched, because "is this
mentioned as history" is not something a regex can decide.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from _paths import PACKAGE, REPO, TESTS

DOCS = ("CLAUDE.md", "README.md")

#: Paths the docs name in order to say they are gone. Each is deliberate prose.
DELETED_ON_PURPOSE = {
    "lineup.py", "auction.py", "strategy.py", "models.py",  # the Classic cluster, W2
    "analyze_qi_bias.py", "resolve_aste_live.py",           # the retired scripts
    "data/storage_state.json",                              # written only by a path nothing takes
    "scripts/_db.py",
}

#: `<phase>-plan.md` style templates, which name no single file.
TEMPLATE = re.compile(r"^-?(plan|todo|spec)\.md$|<")


def _commands(doc: str) -> set[str]:
    """Every `fantabot <group> <command>` shown in a fenced block or inline."""
    return {
        " ".join(m.group(1).split())
        for line in (REPO / doc).read_text().splitlines()
        if (m := re.match(r"^fantabot ((?:[a-z][a-z-]*)(?: [a-z][a-z-]*)?)", line.strip()))
    }


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout
    return set(out.split())


@pytest.mark.parametrize("doc", DOCS)
def test_every_path_named_is_a_file_that_exists(doc: str) -> None:
    tracked = _tracked()
    basenames = {p.rsplit("/", 1)[-1] for p in tracked}
    stale = []
    for number, line in enumerate((REPO / doc).read_text().splitlines(), 1):
        for match in re.finditer(r"`([A-Za-z0-9_./-]+\.(?:py|md|json|toml|sh|jsonl))`", line):
            ref = match.group(1)
            # Package-relative too: the architecture section spells modules as
            # `domain/asta/sentiment.py`, which is how a reader of that tree names them.
            if (
                ref in tracked
                or (REPO / ref).exists()
                or (PACKAGE / ref).exists()
                or (TESTS / ref).exists()
                or ref in DELETED_ON_PURPOSE
                or ref.rsplit("/", 1)[-1] in DELETED_ON_PURPOSE
                or TEMPLATE.match(ref)
                or ref in basenames
            ):
                continue
            stale.append(f"{doc}:{number}: {ref}")

    assert stale == [], "these name files that do not exist:\n  " + "\n  ".join(stale)


def test_the_docs_between_them_show_the_whole_command_surface() -> None:
    """A floor, so the regex below cannot quietly stop matching and pass over nothing."""
    assert len(_commands("CLAUDE.md") | _commands("README.md")) >= 18


@pytest.mark.parametrize("doc", DOCS)
def test_every_command_shown_resolves(doc: str) -> None:
    """`--help` only. Running them would need a database, a browser and an agent."""
    shown = sorted(_commands(doc))
    assert len(shown) >= 5, f"only found {len(shown)} commands; the regex has stopped matching"

    broken = [
        command
        for command in shown
        if subprocess.run(
            ["fantabot", *command.split(), "--help"], cwd=REPO, capture_output=True
        ).returncode
    ]

    assert broken == [], f"documented but do not resolve: {broken}"

"""Every markdown link in a tracked file resolves.

Specs and plans point at each other, at source files and at archived phases, and this
repository has already lost that thread twice: four references to `tasks/plan.md` and
`tasks/todo.md` had rotted by 2026-08-28 because those two filenames are reused by every
phase, and the token-store spec survived only in git history because it was archived to
`docs/`, which `.gitignore` excludes.

**Tracked files only, and tracked targets only**, for that second reason. A link into
`docs/` looks fine on the machine that wrote it and is broken for everyone else; checking
against the working tree would reproduce exactly the blindness that lost the spec.

Anchors (`#section`) are not verified -- only that the file exists. Checking headings
would need a markdown parser and would fail on every renamed section, which is noise
rather than rot.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from _paths import REPO

#: Files that exist on disk, are cited by the specs, and are **not tracked** because
#: `.gitignore` excludes `docs/`. `tasks/archive/simplification-spec.md` §"Decisions taken" §4 records the call
#: deliberately: no credential is in any of them, so the exposure is a working recipe
#: against a third party's live service rather than a secret, and whether to track them is
#: a live decision that phase did not make.
#:
#: They are listed rather than skipped so the set is bounded. A link into one of these is
#: allowed; a link into a *new* untracked file fails, because that is how a document comes
#: to survive on one machine only -- which is how the token-store spec was nearly lost.
UNTRACKED_BY_DECISION = frozenset({
    "docs/fantalab/00-asta-e-requisiti-cli.md",
    "docs/fantalab/01-auction-engine.md",
    "docs/fantalab/02-data-model.md",
    "docs/fantalab/03-platform-map.md",
    "docs/fantalab/04-simulator-spec.md",
    "docs/fantalab/06-asta-write-path.md",
    "docs/fantalab/README.md",
})

#: `[text](target)`, skipping images and reference-style definitions.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)")

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def _tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=REPO, capture_output=True, text=True)
    return sorted(out.stdout.split())


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
    return set(out.stdout.split())


@pytest.mark.parametrize("doc", _tracked_markdown())
def test_every_markdown_link_resolves(doc: str) -> None:
    tracked = _tracked()
    here = (REPO / doc).parent
    broken = []

    for number, line in enumerate((REPO / doc).read_text(encoding="utf-8").splitlines(), 1):
        for match in LINK.finditer(line):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(SKIP_PREFIXES):
                continue
            resolved = (here / target).resolve()
            try:
                relative = resolved.relative_to(REPO)
            except ValueError:
                broken.append(f"{doc}:{number}: {target} (outside the repository)")
                continue
            if str(relative) in UNTRACKED_BY_DECISION:
                continue
            if str(relative) not in tracked and not resolved.is_dir():
                broken.append(f"{doc}:{number}: {target}")

    assert broken == [], "these links do not resolve to a tracked file:\n  " + "\n  ".join(broken)


def test_the_untracked_set_still_describes_reality() -> None:
    """Each file is present and still untracked, or the list has become fiction.

    Both directions matter. One that has gone is a citation to nothing; one that has been
    tracked should leave the list, or the exemption outlives the reason for it.

    **The presence check is maintainer-local.** These files are gitignored (`docs/`), so a
    fresh checkout — CI, or anyone but the machine that wrote them — legitimately does not have
    them, and asserting they are on disk there would be asserting the `.gitignore` is broken.
    The "no longer on disk" direction therefore only runs where the docs actually live; the
    "must not become tracked" direction runs everywhere, because a tracked file drifting into
    this list is real rot regardless of host.
    """
    tracked = _tracked()
    now_tracked = sorted(f for f in UNTRACKED_BY_DECISION if f in tracked)
    assert now_tracked == [], f"now tracked, so remove from the exemption list: {now_tracked}"

    present = [f for f in UNTRACKED_BY_DECISION if (REPO / f).exists()]
    if not present:
        pytest.skip("docs/ not checked out (a fresh/CI clone) — this is a maintainer-local check")
    gone = sorted(f for f in UNTRACKED_BY_DECISION if not (REPO / f).exists())
    assert gone == [], f"cited, exempted, and no longer on disk: {gone}"

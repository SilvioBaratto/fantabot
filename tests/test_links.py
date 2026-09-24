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

**Code is not prose.** `LINK` used to run over the raw line, so a document that *shows*
what a link looks like -- inside backticks, or in a fenced block -- was read as making one.
`AUDIT-2026-09-24.md` tripped it that way and was reworded to get the suite green, which
fixed the sentence and left the regex wrong; the next document to quote a link, and any
widening of this scan to new file types, would hit it again. Fences and inline code spans
are skipped now, so the wording is free again.

That skipping is itself a blindness, and it is bounded rather than trusted: a fence that
never closes swallows the rest of the file, so
`test_no_markdown_file_hides_a_link_behind_an_unterminated_fence` checks that the swallowed
region contains no link. `app/CLAUDE.md:252` opens exactly such a fence today.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator

import pytest
from _paths import REPO

#: Files that exist on disk, are cited by the specs, and are **not tracked** because
#: `.gitignore` excludes `docs/`. The simplification phase's spec -- archived, and on no
#: disk in this checkout: `tasks/` has always been gitignored, so those documents are not
#: in git history either -- recorded the call deliberately in its §"Decisions taken" §4:
#: no credential is in any of them, so the exposure is a working recipe against a third
#: party's live service rather than a secret, and whether to track them is a live decision
#: that phase did not make.
#:
#: They are listed rather than skipped so the set is bounded. A link into one of these is
#: allowed; a link into a *new* untracked file fails, because that is how a document comes
#: to survive on one machine only -- which is how the token-store spec was nearly lost.
UNTRACKED_BY_DECISION = frozenset({
    # Emptied 2026-09-24: `docs/` is tracked now, so nothing under it needs this. The two
    # files still out are binaries no markdown links to. Kept as an empty ratchet rather
    # than deleted -- the mechanism (a link to a file a deliberate decision keeps out of
    # git) can recur, and the assertion below is what would make the next one visible.
})

#: `[text](target)`, skipping images and reference-style definitions.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)")

#: A fenced block delimiter: three or more backticks or tildes, indented at most three
#: spaces. Group 1 is the run, group 2 whatever follows it -- an info string on an opener,
#: and nothing at all on a closer, which is what CommonMark requires of a close.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")

#: An inline code span, matched **conservatively**: a run of backticks, content with no
#: backtick in it, a run of backticks. Erring toward matching too little is the safe
#: direction here -- an unrecognised span leaves its line scanned, which at worst reports a
#: link that is not one, while an over-eager one would delete a real link from the corpus
#: and report nothing. A run stops at the first backtick, so it cannot swallow the prose
#: between two separate spans on one line, and no link *target* can contain a backtick, so
#: stripping a span never removes a target. A span inside link *text* -- ``[`x`](y.md)`` --
#: leaves `[](y.md)`, which `LINK` still matches, because its text group is `[^\]]*`.
CODE_SPAN = re.compile(r"`+[^`]*`+")

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def _prose(text: str) -> Iterator[tuple[int, str]]:
    """Each line that is prose, with its inline code spans blanked out.

    Fenced blocks are dropped whole. A fence closes only on the same delimiter character,
    at least as long, with nothing after it, so a ```` ``` ```` line inside a ```` ```` ````
    block stays content and a `~~~` does not close a backtick fence.
    """
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        delimiter = FENCE.match(line)
        if fence is not None:
            if (
                delimiter
                and delimiter.group(1)[0] == fence[0]
                and len(delimiter.group(1)) >= len(fence)
                and not delimiter.group(2).strip()
            ):
                fence = None
            continue
        if delimiter:
            fence = delimiter.group(1)
            continue
        yield number, CODE_SPAN.sub(" ", line)


def _links(text: str) -> list[tuple[int, str]]:
    """Every markdown link target the text really makes, as `(line number, target)`."""
    return [(number, match.group(1)) for number, line in _prose(text) for match in LINK.finditer(line)]


def _unterminated_from(text: str) -> int | None:
    """The line a fence opens on and never closes, or None. That region is unscanned."""
    fence: str | None = None
    opened: int | None = None
    for number, line in enumerate(text.splitlines(), 1):
        delimiter = FENCE.match(line)
        if fence is not None:
            if (
                delimiter
                and delimiter.group(1)[0] == fence[0]
                and len(delimiter.group(1)) >= len(fence)
                and not delimiter.group(2).strip()
            ):
                fence, opened = None, None
            continue
        if delimiter:
            fence, opened = delimiter.group(1), number
    return opened


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

    for number, target in _links((REPO / doc).read_text(encoding="utf-8")):
        target = target.split("#")[0]
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


@pytest.mark.parametrize("doc", _tracked_markdown())
def test_no_markdown_file_hides_a_link_behind_an_unterminated_fence(doc: str) -> None:
    """Skipping fences is a blindness; this is what bounds it.

    A fence that never closes runs to the end of the document -- that is what CommonMark
    says and what a renderer does -- so everything after it stops being scanned. That is
    correct for a block of shell transcript and catastrophic for a file whose links happen
    to sit below a missing ```` ``` ````, and the difference is invisible from a green run.

    Checked rather than exempted, so no list needs maintaining: an unterminated fence is
    fine, and an unterminated fence with a link under it is not. `app/CLAUDE.md:252` opens
    one today and has no links at all, so this passes on evidence rather than by luck.
    """
    text = (REPO / doc).read_text(encoding="utf-8")
    opened = _unterminated_from(text)
    if opened is None:
        return

    hidden = [
        f"{doc}:{number}: {match.group(1)}"
        for number, line in enumerate(text.splitlines(), 1)
        if number > opened
        for match in LINK.finditer(line)
    ]

    assert hidden == [], (
        f"{doc}:{opened} opens a fence that never closes, so everything below it is "
        "unscanned -- and these links are down there:\n  " + "\n  ".join(hidden)
    )


def test_an_unterminated_fence_is_detected_at_all() -> None:
    """The guard above is a no-op wherever `_unterminated_from` answers None.

    `app/CLAUDE.md` is the only file in the corpus that trips it, and it holds no links, so
    the guard would look identical if the detector simply never fired. Pinned directly
    instead.
    """
    assert _unterminated_from("```\ncode\n") == 1
    assert _unterminated_from("prose\n\n````\ncode\n```\n") == 3
    assert _unterminated_from("```\ncode\n```\n") is None
    assert _unterminated_from("no fences here\n") is None
    assert _unterminated_from((REPO / "app" / "CLAUDE.md").read_text(encoding="utf-8")) == 252


def test_a_link_shown_as_an_example_is_not_a_link() -> None:
    """The defect this scan carried: a document that *quotes* a link was read as making one.

    `AUDIT-2026-09-24.md` was reworded to get past it, which repaired the sentence and left
    the scan wrong -- so the evidence has to be a case the corpus does not supply, or the
    next rewording is the fix again.
    """
    assert _links("see `[text](tasks/plan.md)` for the shape\n") == []
    assert _links("a double span: ``[a](b.md)`` and that is all\n") == []
    assert _links("```\n[a](gone.md)\n```\n") == []
    assert _links("~~~markdown\n[a](gone.md)\n~~~\n") == []
    assert _links("```bash\ncat '[a](gone.md)'\n```\n") == []
    # a longer fence is not closed by a shorter run, nor by the other delimiter character
    assert _links("````\n```\n[a](gone.md)\n```\n````\n") == []
    assert _links("```\n~~~\n[a](gone.md)\n~~~\n```\n") == []
    # an info string on the closing run means it is not a close
    assert _links("```\n``` text\n[a](gone.md)\n```\n") == []


def test_a_real_link_is_still_found() -> None:
    """The other half, so the skipping above cannot go vacuous by matching nothing.

    Three shapes the corpus really uses, and the two that sit *beside* code: a span earlier
    on the line, and a span inside the link text. A stripper that took the line with it
    would pass the test above and silently stop checking the repository.
    """
    assert _links("see [the spec](tasks/plan.md) for the shape\n") == [(1, "tasks/plan.md")]
    assert _links("`asta bid` is in [the spec](tasks/plan.md).\n") == [(1, "tasks/plan.md")]
    assert _links("[`domain/asta/bid.py`](src/fantabot/domain/asta/bid.py) is pure\n") == [
        (1, "src/fantabot/domain/asta/bid.py")
    ]
    assert _links("after a block:\n```\ncode\n```\n[a](b.md)\n") == [(5, "b.md")]
    assert _links("![img](x.png) but [real](y.md)\n") == [(1, "y.md")]
    # an unclosed backtick strips nothing, so the link on that line is still checked
    assert _links("a stray ` tick and [real](y.md)\n") == [(1, "y.md")]
    # four spaces of indent is an indented code block, not a fence delimiter, so a run of
    # backticks there opens nothing and the lines around it stay scanned. Erring toward
    # scanning again: the cost is a spurious report, and the cost of the other choice is a
    # whole region of the file silently dropped by a stray indent.
    assert _links("    ```\n    [a](b.md)\n    ```\n") == [(2, "b.md")]


def test_the_scan_reads_the_same_corpus_it_did_before_fences_were_skipped() -> None:
    """A floor on the whole repository, so "skip code" cannot quietly become "skip".

    Measured 2026-09-24: 21 link matches across every tracked `*.md`, identical before and
    after the change -- nothing the old scan checked is checked less now. A floor rather
    than an equality because documents gain links; the point is that it cannot collapse.
    """
    found = sum(len(_links((REPO / doc).read_text(encoding="utf-8"))) for doc in _tracked_markdown())

    assert found >= 21, f"only {found} links found across the corpus; the scan has stopped matching"


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

"""`CLAUDE.md` and `README.md` describe the tree that exists.

SC 27, checked as two things rather than read over: every file path they name is a file,
and every command they show answers `--help`. Documentation rots silently -- a path that
moved still reads correctly -- and this phase moved 94 modules, so a doc check that is a
human re-reading is a doc check that happens once.

**Every reference is a claim a particular document makes, so every table here is keyed by
`(document, the path that document writes)`.** That is not ceremony. `lineup.py` appears
bare in both docs and means two different files: `README.md:84` names the deleted Classic
scaffolding, and `CLAUDE.md:138` names the live `src/fantabot/interface/lineup.py` in the
same breath as `asta.py`. A table keyed by the ref alone has to answer one of those two
sentences wrongly, and the one it answered wrongly was the live file.

A path is checked as the whole path. This used to fall back to "any tracked file anywhere
with this basename", which passed every file that had *moved* -- the one thing this module
exists to catch, and why almost every Tier-4 finding in `AUDIT-2026-09-24.md` was invisible
here. `BY_CONTEXT` replaces that fallback for the bare basenames the prose really does
name, by pinning each to the single file its sentence means.

**`DELETED_ON_PURPOSE` was the same fallback, one line further down.** It matched on
basename too, so any ref *ending* in one of its eight names was not weakly checked but
never checked at all -- and two of the eight, `lineup.py` and `models.py`, are the
basenames of seven live tracked files. `CLAUDE.md:235` was already sitting in that hole:
`interface/lineup.py` resolves perfectly well, and was being waved through by a list of
things that supposedly do not exist, so a move of that module would not have been noticed.
It is now the expected set of `test_every_path_named_is_a_file_that_exists` -- an
`EXPECTED_*`-style ratchet in the sense `tests/test_layers.py` uses, compared with `==`, so
an entry whose file comes back, or whose sentence was repaired, fails until it is deleted.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from _paths import PACKAGE, REPO, TESTS

DOCS = ("CLAUDE.md", "README.md")

#: Paths a doc names **in order to say they are gone**, as `(document, the ref that
#: document writes)`. Not a list of things to skip: it is the *expected* output of
#: `test_every_path_named_is_a_file_that_exists`, compared with `==` like
#: `tests/test_layers.py`'s `EXPECTED_*` sets. An entry that starts resolving -- the file
#: recreated, or the doc rewritten to name a live path -- stops being produced by that
#: computation and fails the comparison as *unused*, so the only way back to green is to
#: delete it. Nothing here can shadow a live file: a pair whose ref resolves is a pair the
#: test refuses to accept.
#:
#: The eight basenames this replaces, each checked against `git ls-files`:
#:
#: * `lineup.py` -- real deletion **only as README.md:84 writes it** (the W2 Classic
#:   scaffolding). As a basename it shadowed `src/fantabot/interface/lineup.py` and
#:   `app/fantabot_app/api/v1/endpoints/lineup.py`, and swallowed CLAUDE.md:138's bare
#:   mention of the live module; that one is in `BY_CONTEXT` now, where `asta.py` -- the
#:   other half of the same sentence -- already was.
#: * `auction.py`, `strategy.py` -- real deletions, no tracked file of either name. Kept.
#: * `models.py` -- **named by neither doc**, and shadowed all five of
#:   `domain/{harvest,lega,lineup,mantra,news}/models.py`. Pure liability; deleted.
#: * `resolve_aste_live.py` -- **named by neither doc**, and `scripts/resolve_aste_live.py`
#:   is tracked and alive: the entry described a deletion that was reverted. Deleted.
#: * `analyze_qi_bias.py`, `scripts/_db.py` -- really gone, but named by neither doc any
#:   more. Dead entries, and a dead entry is what the next file of that name inherits.
#: * `data/storage_state.json` -- not a deletion at all. README.md:136 says it is *opt-in*
#:   and that the default run does not create it, and `.gitignore:12` (`data/*`) excludes
#:   it either way. Absent today, present the moment an operator runs `--save-session`, so
#:   a "must stay gone" ratchet on it would go red for the operator's doing. It moved to
#:   `GITIGNORED_BY_POLICY`, whose premise is the one that is actually true of it.
DELETED_ON_PURPOSE: set[tuple[str, str]] = {
    # README.md:84, the paragraph on the Classic scaffolding removed in W2 rather than
    # left raising `NotImplementedError` against a DOM nobody mapped.
    ("README.md", "lineup.py"),
    ("README.md", "auction.py"),
    ("README.md", "strategy.py"),
}

#: `<phase>-plan.md` style templates, which name no single file. `^-?(plan|todo|spec)\.md$`
#: only ever matches a whole ref, so it cannot shadow `foo/spec.md`.
#:
#: The `|<` alternative this used to carry was dead and is gone. `REF` below captures from
#: `[A-Za-z0-9_./-]`, which has no `<`, so no ref containing one is producible: in
#: ``to `tasks/archive/<phase>-spec.md`, `-plan.md` and `-todo.md`.`` the first span never
#: matches at all and the two that do are the bare suffixes. Removing an alternative that
#: cannot fire narrows nothing -- but a dead exemption branch is a live one the day `REF`
#: gains a character, and that is not a change anyone would think to re-audit here.
TEMPLATE = re.compile(r"^-?(?:plan|todo|spec)\.md$")

#: Names of the *convention*, not of files. `SPEC.md`, `tasks/plan.md` and
#: `tasks/todo.md` are what a phase in flight is called; between phases they correctly do
#: not exist, and the paragraph naming them is the rule that says so. Exempting them by
#: name rather than by pattern keeps the exemption to these three.
#:
#: Deliberately **not** ratcheted on absence, unlike `DELETED_ON_PURPOSE`: mid-phase all
#: three are on disk (they are now), between phases none are, and both states are correct.
#: What is ratcheted is that none of them is ever a *tracked* file -- see
#: `test_no_exemption_covers_a_file_that_is_actually_in_the_repository`.
CONVENTIONAL = {"SPEC.md", "tasks/plan.md", "tasks/todo.md"}

#: Paths the docs cite that **git cannot see**, because this repository's own `.gitignore`
#: excludes them: `docs/` is the local-only maintainer-notes tree (`.gitignore:27`), and
#: `data/storage_state.json` is the opt-in Playwright session (`.gitignore:12`, `data/*`).
#: A fresh checkout or CI legitimately has neither, so `git ls-files` can never confirm one
#: and a reference into them is not verifiable in git.
#:
#: An entry ending in `/` is a prefix; anything else is an exact ref. The premise is
#: asserted rather than assumed -- `test_the_gitignored_exemption_still_has_its_reason`
#: makes git itself answer "do you ignore this?", so an entry that stops being ignored
#: stops being exempt. That is the difference between this and the basename fallback it
#: keeps company with: a prefix is as blind as a basename unless something checks that it
#: covers nothing live, which
#: `test_no_exemption_covers_a_file_that_is_actually_in_the_repository` does.
GITIGNORED_BY_POLICY = ("docs/", "data/storage_state.json")

#: Bare basenames the docs name in prose, mapped to **the one tracked file each means**,
#: keyed by `(document, ref)`.
#:
#: This replaces a `ref in basenames` fallback -- "any tracked file anywhere sharing this
#: basename" -- which passed a moved file, which is precisely the rot this module exists to
#: catch (audit 1.7; it is why almost every Tier-4 finding in `AUDIT-2026-09-24.md` was
#: invisible here). Several keys are genuinely ambiguous on disk -- `asta.py`, `roles.py`,
#: `state.py` and `lineup.py` each name two or more tracked files -- so the old fallback
#: was answering "some file, somewhere" for references whose sentence means exactly one.
#:
#: A bare basename in prose is not itself rot: the Classic paragraph in CLAUDE.md reads
#: "(`roles.py`)" having just named `domain/classic/`, and spelling the whole path there
#: would make the sentence worse, not truer. It is the directory the *sentence* supplies and
#: the guard could not see. Naming it here puts it back, and makes this a ratchet in both
#: directions:
#:
#: * move or delete the mapped file and this goes red, even though another file of that
#:   name still exists -- the exact case the fallback let through;
#: * spell the full path in the doc instead, and `test_every_context_entry_is_still_needed`
#:   goes red until the entry here is deleted too. `==`, not `>=`, for audit 1.6's reason.
#: The paragraph is named, not its line number: these two files are edited often enough
#: that a line citation here would be the next thing to rot.
BY_CONTEXT = {
    # CLAUDE.md, the `tests/_testtree.py` paragraph, on where two test files land.
    ("CLAUDE.md", "test_token_store.py"): "tests/adapters/tokens/test_token_store.py",
    ("CLAUDE.md", "test_state.py"): "tests/adapters/browser/test_state.py",
    # CLAUDE.md, the writing ratchet's two entries, both `interface/` modules. Both
    # basenames are ambiguous: `app/fantabot_app/api/v1/endpoints/` has an `asta.py` and a
    # `lineup.py` of its own. `lineup.py` sat in `DELETED_ON_PURPOSE` until 2026-09-24,
    # which is how a live module came to be exempted as a deleted one.
    ("CLAUDE.md", "asta.py"): "src/fantabot/interface/asta.py",
    ("CLAUDE.md", "lineup.py"): "src/fantabot/interface/lineup.py",
    # CLAUDE.md's lineup and `mantra_compat` paragraphs, and README.md's Mantra-grid
    # section -- the two Mantra artefacts, named as the package data they ship as.
    ("CLAUDE.md", "mantra_schemi.json"): "src/fantabot/data/mantra_schemi.json",
    ("CLAUDE.md", "mantra_compat.json"): "src/fantabot/data/mantra_compat.json",
    ("README.md", "mantra_compat.json"): "src/fantabot/data/mantra_compat.json",
    # CLAUDE.md, the `domain/classic/` paragraph. `roles.py` and `state.py` are both
    # ambiguous: `domain/asta/` has one of each, and they are the *other* seam -- exactly
    # the pair a basename fallback would have resolved to the wrong one of.
    ("CLAUDE.md", "roles.py"): "src/fantabot/domain/classic/roles.py",
    ("CLAUDE.md", "formations.py"): "src/fantabot/domain/classic/formations.py",
    ("CLAUDE.md", "state.py"): "src/fantabot/domain/classic/state.py",
    # CLAUDE.md, the token-secrecy paragraph's `DECRYPT_RESERVED` entry.
    ("CLAUDE.md", "apileague.py"): "src/fantabot/adapters/http/apileague.py",
    # CLAUDE.md, the `DEFAULT_SEASONS` paragraph, on the three scrapers.
    ("CLAUDE.md", "voti.py"): "src/fantabot/adapters/scraping/voti.py",
    ("CLAUDE.md", "statistiche.py"): "src/fantabot/adapters/scraping/statistiche.py",
    ("CLAUDE.md", "quotazioni.py"): "src/fantabot/adapters/scraping/quotazioni.py",
}

#: A backticked path, with an optional `:12` or `:12-20` line reference. The line suffix is
#: matched and discarded rather than left unmatched: `domain/asta/state.py:44` is a path
#: claim like any other, and leaving it out of the regex left two of them unchecked.
REF = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|md|json|toml|sh|jsonl))(?::\d+(?:-\d+)?)?`")


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


def _refs(doc: str) -> list[tuple[int, str]]:
    """Every backticked path the doc names, as `(line number, ref)`."""
    return [
        (number, match.group(1))
        for number, line in enumerate((REPO / doc).read_text().splitlines(), 1)
        for match in REF.finditer(line)
    ]


def _claims() -> dict[tuple[str, str], list[int]]:
    """Every `(document, ref)` the docs make, with the lines that make it."""
    claims: dict[tuple[str, str], list[int]] = {}
    for doc in DOCS:
        for number, ref in _refs(doc):
            claims.setdefault((doc, ref), []).append(number)
    return claims


def _gitignored_by_policy(ref: str) -> bool:
    """A prefix entry ends in `/`; anything else must match the whole ref."""
    return any(
        ref.startswith(entry) if entry.endswith("/") else ref == entry
        for entry in GITIGNORED_BY_POLICY
    )


def _exempt(ref: str) -> bool:
    """References that are not path claims about the tree, so `_resolves` is not asked.

    Deliberately **no `DELETED_ON_PURPOSE` branch**. A deletion is a claim about the tree
    -- "this is not there" -- so it is checked rather than skipped, as the expected set of
    `test_every_path_named_is_a_file_that_exists`. While it lived here it was matched on
    basename, and an exemption is the one place a basename match is unrecoverable: a
    `_resolves` that answers "some file, somewhere" is at least still asked the question.
    """
    return bool(TEMPLATE.match(ref)) or ref in CONVENTIONAL or _gitignored_by_policy(ref)


def _resolves(doc: str, ref: str, tracked: set[str]) -> bool:
    """Is *ref* a real file, at the path it names -- or at the one `BY_CONTEXT` pins it to?

    Package-relative and tests-relative too: the architecture section spells modules as
    `domain/asta/sentiment.py`, which is how a reader of that tree names them.

    There is deliberately **no basename fallback** here. `ref in basenames` -- any tracked
    file anywhere with this name -- passed every file that had moved, which is the one
    thing this test was written to catch.
    """
    target = BY_CONTEXT.get((doc, ref), ref)
    return (
        target in tracked
        or (REPO / target).exists()
        or (PACKAGE / target).exists()
        or (TESTS / target).exists()
    )


def _unresolved(tracked: set[str], claims: dict[tuple[str, str], list[int]]) -> set[tuple[str, str]]:
    """Every `(document, ref)` claim that is checked and does not come out true.

    Extracted so `test_an_unlisted_stale_reference_is_reported` can drive it over a
    synthetic pair of documents. The `==` below can be satisfied two ways -- by reporting
    exactly the deliberate deletions, or by reporting nothing and shrinking the expected
    set to match -- and nothing in the repository as it stands tells those apart, because
    the tree is supposed to be clean. A `missing & DELETED_ON_PURPOSE` dropped in here
    keeps all twelve tests green while silently discarding every new stale ref, so the
    reporting half needs a case of its own with a ref that really is stale.
    """
    return {key for key in claims if not _exempt(key[1]) and not _resolves(*key, tracked)}


def test_every_path_named_is_a_file_that_exists() -> None:
    """One `==` over both docs, with `DELETED_ON_PURPOSE` as the expected set.

    Not two guards. "No unlisted ref is stale" and "no listed entry has been repaired" are
    the two directions of a single comparison, and split into a pair of tests the second
    one is free to pass while the first does the work -- which is how a stale exemption
    survives. `tests/test_layers.py`'s `EXPECTED_*` sets are the same shape for the same
    reason.
    """
    tracked = _tracked()
    claims = _claims()
    missing = _unresolved(tracked, claims)

    def show(keys: set[tuple[str, str]]) -> str:
        return "\n    ".join(
            f"{doc}:{','.join(map(str, claims.get((doc, ref), [])))}: {ref}"
            for doc, ref in sorted(keys)
        )

    assert missing == DELETED_ON_PURPOSE, (
        "\n  these name files that do not exist, and are not listed as deliberate:\n    "
        + (show(missing - DELETED_ON_PURPOSE) or "(none)")
        + "\n  these are listed as deliberately-deleted but now resolve, or the doc no "
        "longer names them -- delete the entry:\n    "
        + (show(DELETED_ON_PURPOSE - missing) or "(none)")
    )


def test_an_unlisted_stale_reference_is_reported(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A stale ref that nobody listed comes out of `_unresolved`, and a live one does not.

    The two documents here are synthetic on purpose: the real ones are meant to be clean,
    so the test above proves "nothing is stale" against a tree where nothing is, and would
    go on passing if the computation stopped looking. The moved path is deliberately one
    ending in `models.py` -- a basename `DELETED_ON_PURPOSE` used to hold, and therefore
    exactly the ref the old exemption swallowed whole.
    """
    module = sys.modules[__name__]
    (tmp_path / "CLAUDE.md").write_text(
        "the live one is `src/fantabot/interface/lineup.py`,\n"
        "this one has moved: `src/fantabot/domain/typo/models.py`,\n"
        "and this is a real deletion: `auction.py`.\n"
    )
    (tmp_path / "README.md").write_text("`docs/leghe-api.md` stays exempt.\n")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "PACKAGE", empty)
    monkeypatch.setattr(module, "TESTS", empty)

    assert _unresolved({"src/fantabot/interface/lineup.py"}, _claims()) == {
        ("CLAUDE.md", "src/fantabot/domain/typo/models.py"),
        ("CLAUDE.md", "auction.py"),
    }


def _resolves_unaided(ref: str, tracked: set[str]) -> bool:
    """`_resolves` with `BY_CONTEXT` taken away -- what the doc's own text can prove."""
    return (
        ref in tracked
        or (REPO / ref).exists()
        or (PACKAGE / ref).exists()
        or (TESTS / ref).exists()
    )


def test_every_context_entry_is_still_needed() -> None:
    """`BY_CONTEXT` is a ratchet, so it holds exactly what the docs currently rely on it for.

    Both directions in one `==`. A basename the docs lean on and the table does not list
    fails the test above as well; an entry whose doc reference has been rewritten as a full
    path, or deleted, fails only here -- and it has to, because an exemption nobody is
    watching is one the next bare basename of that name inherits silently. `==`, not `>=`:
    a list that only ever grows stops meaning anything (`app/fantabot_app/api/outcomes.py`,
    and audit finding 1.6).

    `DELETED_ON_PURPOSE` is subtracted, not exempted: those refs also fail to resolve
    unaided -- that is what makes them deletions -- and demanding a pin for them would be
    demanding a live file to point a deletion at.
    """
    tracked = _tracked()
    needed = {
        key
        for key in _claims()
        if not _exempt(key[1]) and not _resolves_unaided(key[1], tracked)
    } - DELETED_ON_PURPOSE

    assert needed == set(BY_CONTEXT), (
        "BY_CONTEXT must name exactly the (doc, basename) pairs the docs still rely on it "
        f"for; unused: {sorted(set(BY_CONTEXT) - needed)}; unlisted: {sorted(needed - set(BY_CONTEXT))}"
    )


def test_a_ref_whose_directory_is_wrong_does_not_pass_on_its_basename() -> None:
    """The shape the dropped `ref in basenames` fallback let through, pinned directly.

    Without this, re-adding that fallback is invisible: every ref in the two docs would go
    on passing, and the test above would stay green while checking a strictly weaker thing.
    All three refs below name a basename that is tracked somewhere; only the first names it
    where the file actually is. The third also pins that `BY_CONTEXT` is keyed by the whole
    ref and not by its basename -- `formations.py` is a key, and a *path* ending in it is
    not covered by that entry.
    """
    tracked = _tracked()

    assert _resolves("CLAUDE.md", "src/fantabot/adapters/http/apileague.py", tracked)
    assert not _resolves("CLAUDE.md", "src/fantabot/adapters/persistence/apileague.py", tracked)
    assert not _resolves("CLAUDE.md", "src/fantabot/domain/asta/formations.py", tracked)


def test_a_context_pin_belongs_to_the_document_that_makes_it() -> None:
    """`BY_CONTEXT` is keyed by `(doc, ref)`, and that keying is load-bearing.

    `lineup.py` is the case: CLAUDE.md:138 names the live `interface/lineup.py` beside
    `asta.py`, and README.md:84 names the deleted Classic scaffolding. One table keyed by
    the ref alone answers both sentences the same way, so pinning CLAUDE's mention would
    silently resolve README's deletion too -- and the deletion would drop out of
    `DELETED_ON_PURPOSE`'s `==` as "unused", inviting someone to delete the entry that is
    still true.
    """
    tracked = _tracked()

    assert _resolves("CLAUDE.md", "lineup.py", tracked)
    assert not _resolves("README.md", "lineup.py", tracked)
    assert ("README.md", "lineup.py") in DELETED_ON_PURPOSE


def _names_the_same_file(target: str, ref: str) -> bool:
    return target == ref or target.endswith("/" + ref)


def test_every_context_entry_points_at_a_tracked_file_of_that_name() -> None:
    """The pin is what makes the table stricter than the fallback it replaced: one path,
    tracked, and actually called what the doc calls it."""
    tracked = _tracked()
    wrong = [
        f"{doc}: {ref} -> {target}"
        for (doc, ref), target in sorted(BY_CONTEXT.items())
        if target not in tracked or not _names_the_same_file(target, ref)
    ]

    assert wrong == [], f"BY_CONTEXT entries that do not resolve, or are misnamed: {wrong}"


def test_every_context_entry_is_pinned_by_a_document_that_exists() -> None:
    """A pin keyed to a document not in `DOCS` is a pin nothing can ever consult."""
    keyed = {doc for doc, _ref in BY_CONTEXT} | {doc for doc, _ref in DELETED_ON_PURPOSE}
    stray = sorted(keyed - set(DOCS))
    assert stray == [], f"keyed to documents this module does not read: {stray}"


def test_no_exemption_covers_a_file_that_is_actually_in_the_repository() -> None:
    """The class the basename fallback belonged to, closed branch-agnostically.

    Whatever an exemption is keyed on -- a basename, a prefix, a regex, a literal -- if the
    string it matches is a path git tracks, then that path is one this module can no longer
    see move. Enumerating `git ls-files` rather than the docs' refs is the point: it asks
    the question of every file in the repository at once, so a future loose branch fails
    here without anyone having to think of the example that would expose it.

    Measured against the basename fallback this replaces, it reports eight files:
    `src/fantabot/interface/lineup.py`, `app/fantabot_app/api/v1/endpoints/lineup.py`, the
    five `domain/*/models.py`, and `scripts/resolve_aste_live.py`.
    """
    covered = sorted(path for path in _tracked() if _exempt(path))

    assert covered == [], (
        "these are tracked files, and an exemption is hiding them from the path check:\n  "
        + "\n  ".join(covered)
    )


def test_no_exempt_ref_names_a_source_file() -> None:
    """The same class, read from the docs' side, where `git ls-files` cannot reach.

    `CLAUDE.md:235` writes `interface/lineup.py` -- not a tracked path as spelled, so the
    scan above would not have caught it, yet it resolves package-relative and was exempt.
    `src/` and `tests/` are tracked whole, so a ref that resolves under either names a real
    source file, and no exemption here is about a real source file: templates and
    conventions are phase artefacts, and `GITIGNORED_BY_POLICY` is about paths git ignores.
    """
    tracked = _tracked()
    covered = [
        f"{doc}:{','.join(map(str, lines))}: {ref}"
        for (doc, ref), lines in sorted(_claims().items())
        if _exempt(ref)
        and (ref in tracked or (PACKAGE / ref).exists() or (TESTS / ref).exists())
    ]

    assert covered == [], (
        "these refs name a real source file and are being exempted anyway:\n  "
        + "\n  ".join(covered)
    )


def _git_ignores(path: str) -> bool:
    probe = path + ".probe" if path.endswith("/") else path
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", probe], cwd=REPO, capture_output=True
        ).returncode
        == 0
    )


def test_a_gitignored_entry_covers_a_directory_or_a_file_and_nothing_else() -> None:
    """An entry ending in `/` is a prefix; anything else must match the whole ref.

    Treating every entry as a prefix passes today -- `data/storage_state.json` is the only
    ref that starts with itself -- and is the same silent widening as a basename match one
    entry later: the first exact entry with a common stem would start exempting its
    neighbours. Pinned here rather than left to the corpus, because the corpus cannot see
    it.
    """
    assert _gitignored_by_policy("docs/leghe-api.md")
    assert _gitignored_by_policy("data/storage_state.json")
    # a prefix entry stops at the directory boundary it spells
    assert not _gitignored_by_policy("docsite/leghe-api.md")
    assert not _gitignored_by_policy("src/fantabot/docs/leghe-api.md")
    # an exact entry is not a prefix
    assert not _gitignored_by_policy("data/storage_state.json.bak")
    assert not _gitignored_by_policy("data/storage_state.jsonl")


def test_the_gitignored_exemption_still_has_its_reason() -> None:
    """Git itself answers whether each entry is ignored, rather than the comment asserting it.

    The whole justification for this branch is "a fresh checkout cannot have it, so `git
    ls-files` can never confirm the reference". The day an entry stops being gitignored that
    sentence is false and the ref becomes checkable like any other -- which is a *repair*,
    and the entry has to go for the check to start happening.
    """
    unjustified = sorted(entry for entry in GITIGNORED_BY_POLICY if not _git_ignores(entry))

    assert unjustified == [], (
        "these are exempted as gitignored and git does not ignore them, so the exemption "
        f"has outlived its reason: {unjustified}"
    )


def test_the_docs_between_them_show_the_whole_command_surface() -> None:
    """A floor, so the regex below cannot quietly stop matching and pass over nothing."""
    assert len(_commands("CLAUDE.md") | _commands("README.md")) >= 18


def _resolve(path: list[str]) -> bool:
    """Walk the click command tree the console script exposes."""
    import typer

    from fantabot.interface.app import app

    node = typer.main.get_command(app)
    for name in path:
        commands = getattr(node, "commands", None)
        if not commands or name not in commands:
            return False
        node = commands[name]
    return True


@pytest.mark.parametrize("doc", DOCS)
def test_every_command_shown_resolves(doc: str) -> None:
    """Resolved against the command tree, not by running `fantabot --help` per command.

    Nineteen subprocess launches cost four seconds, which put the default tier at 9.8 s
    against SC 24's ten-second ceiling -- a gate that close to its limit flakes on a busy
    machine, and a flaky gate gets ignored. The tree is the same object the console
    script dispatches on, so it answers the same question in milliseconds.
    """
    shown = sorted(_commands(doc))
    assert len(shown) >= 5, f"only found {len(shown)} commands; the regex has stopped matching"

    broken = [command for command in shown if not _resolve(command.split())]

    assert broken == [], f"documented but do not resolve: {broken}"

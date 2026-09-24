"""The leak guard. SPEC calls this "the one test that matters most".

It lands before any module under ``tokens/`` exists, on purpose. A test that
matters most is not written last: the same move the previous phase made when it
landed ``test_db_boundary.py``'s import scan before a single model existed, and
the reason that boundary has held since.

Source-text and introspection, in the style of ``test_db_boundary.py:29-38`` —
not behaviour. A leak is not something you can reliably provoke at runtime; it is
something you notice a year later in a cron log. So these assertions read the
tree.

Seven assertions. Three were live against the tree the day this landed and three
became live as their targets appeared; the seventh was added 2026-09-24, when
widening assertion 5 to cover `write_text` reported that `auth login` writes every
lega bearer token to `data/storage_state.json` in the clear. That is the designed
session file, so what keeps it off SPEC's Never list is that it cannot be
committed — which had been taken on trust and is now read out of git.

1. no JWT literal in anything git tracks                          — live
2. the key cannot be committed                                    — live
3. no key on argv                                                 — live
4. ``decrypt(`` confined to its two allowed files                 — *(deferred, proved red at T13)*
5. the AST walk over print/log/raise/assert arguments             — *(deferred, proved red at T13)*
6. ``cli.py``'s exclude set names the key                         — live
7. the session file the login writes is git-ignored               — live

This file necessarily contains the patterns it forbids — a JWT-shaped literal to
test assertion 1 against, and the option strings assertion 3 rejects. Every
scan therefore excludes this file by name, and every token in it is
**synthesized**: a real one has never been in this repository and must not be.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import Counter
from pathlib import Path

from _paths import PACKAGE, REPO, module_file, pkgs

SELF = Path(__file__).resolve()

# Every JWT header segment is base64 of `{"`, so it starts `eyJ`. Requiring the
# dot after the segment keeps the pattern from matching prose about JWTs.
JWT_LITERAL = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.")

# SPEC's allowlist, split in two so both halves are checkable. The set is one thing —
# where `decrypt(` may appear — but its entries mean two different things, and a single
# set cannot say which, so a dead entry and a deliberate reservation look identical.
DECRYPT_SITES = {
    "domain/tokens/crypto.py",
    "adapters/tokens/store.py",
    # Phase 5. A second service, so a second store — a lega token is a JWT whose
    # claims we read, a FantaLab session is three opaque strings. Listed here
    # rather than the assertion being widened to `tokens/*`: this test exists to
    # make each new decryption site a deliberate entry, and it did its job.
    "adapters/tokens/fantalab_store.py",
}

#: Allowed, and expected to stay empty. The implementation routes `auth_headers`
#: through `store.load_plaintext`, so `apileague.py` does not decrypt — it is listed so
#: that a later change there fails deliberately, at review, rather than by surprise.
DECRYPT_RESERVED = {"adapters/http/apileague.py"}

DECRYPT_ALLOWED = DECRYPT_SITES | DECRYPT_RESERVED

# argv is visible in `ps` and persists in shell history. SPEC's Never list.
FORBIDDEN_OPTIONS = ("--key", "--encryption-key", "--fernet-key", "--secret", "--token")

# Words that name a credential, or a structure holding one.
#
# Matched **whole and by component** — `_names_a_secret` tries the identifier itself
# first and then its underscore-separated parts. Both halves are load-bearing, and each
# one alone has already been shipped and found wanting:
#
# * whole only (until 2026-09-24): `refresh_token` is what `FantalabSession` really
#   calls one of its three JWTs and `self._token` is what `harvest/client.py` calls its
#   bearer, so an exact match let a print of either straight through.
# * components only (2026-09-24, for one round): `storage_state` splits into `storage`
#   and `state`, neither of which is a word in this set — so the **one** multi-word entry
#   here stopped matching anything at all, and it is the entry naming the raw Playwright
#   blob every other credential below is derived from. Measured: `print(storage_state)`
#   inside `domain/tokens/fantalab.parse_fantalab_storage` went from caught to uncaught,
#   while `print(blob)` in the same function stayed caught. The set said eleven words and
#   the matcher honoured ten.
#
# So the rule is the union of the two, never one of them.
# `test_every_secret_name_matches_itself` pins the whole-identifier half against exactly
# that regression, without needing a source mutation to see it.
SECRET_NAMES = frozenset(
    {
        "token",
        "plaintext",
        "ciphertext",
        "key",
        "headers",
        "auth",
        "bearer",
        "storage_state",
        "blob",
        "entry",
        "payload",
        # The nine below landed 2026-09-24. `config_report` is listed in
        # `LOOSE_TOKEN_HANDLERS` "by breadth ... the Fernet key, the lega password, the
        # agent bearer token and the DSN's password" — and four of those five words were
        # missing from this set, so the breadth argument bought nothing:
        # `print(f"{url.password}")` in that very module was measured surviving.
        "password",
        "secret",
        "credential",
        "credentials",
        # `rtdb.place_raise` builds `params = {"auth": token}` and hands it to httpx as a
        # query string. Its own admission comment says the module is listed *because* of
        # that — but the debug line anyone actually writes names `params`, not `token`.
        "params",
        # A FantaLab session is three opaque strings, and code names a value what it is.
        "session",
        "jwt",
        "cookie",
        "cookies",
        # A DSN carries a password in its userinfo. Two live sites name one and are
        # *not* leaks — both render with `hide_password=True` — so both are ratcheted in
        # `ACCEPTED_EMISSIONS` against that exact evidence: drop the masking and the
        # exemption dies with the reason for it.
        "dsn",
    }
)

#: Functions whose **return value** is a credential.
#:
#: `print(f(x))` is the one shape in this file that is not soundly decidable from the
#: syntax: nothing in the tree says what comes back, and a guard cannot infer it without
#: type information. So this is a **ratchet, not a closure**, and it is the one hole this
#: file does not claim to have shut.
#:
#: Only functions whose own name does not already give them away are listed.
#: `load_plaintext`, `auth_headers`, `bearer_from`, `_headers`, `read_credential` and
#: `as_blob` each carry a word from `SECRET_NAMES` and are caught with no entry here;
#: adding one would be a second gate on the same question, which is how a mutant becomes
#: unobservable. `test_the_credential_returning_ratchet_is_live_and_minimal` asserts both
#: halves — every entry resolves to a `def` in a scanned module, and no entry is
#: redundant — so a rename, a deletion or a duplicate turns it red and the entry must
#: be edited rather than left to rot.
CREDENTIAL_RETURNING = frozenset(
    {
        "decrypt",  # domain/tokens/crypto.py — returns the plaintext bearer token
        "encrypt",  # ...and the ciphertext, which is the same secret under a key
        "read_storage_state",  # adapters/browser/capture.py — the raw Playwright blob
        "parse_fantalab_storage",  # domain/tokens/fantalab.py — all three FantaLab JWTs
        "load",  # adapters/tokens/fantalab_store.py — returns a FantalabSession
        # An *injected* one, and the reason the liveness check below asks for a bound
        # name rather than a `def`: `fantalab_login.run` takes `read_state: StateReader`
        # and every value it returns is the raw browser blob. A seam does not make the
        # thing that comes back through it less of a credential.
        "read_state",
    }
)

#: Emissions the walk reports that are **not** leaks.
#:
#: A ratchet, not an exemption. Each entry carries the *evidence* that makes it safe, and
#: the assertion checks that the evidence is still in the module — so removing the thing
#: that made it safe turns this red rather than leaving a stale allowance behind. Fixing
#: or deleting the site changes the count, which is also red, and then the entry goes too.
#:
#: Keyed `<module>:<identifier>` **with a count**, never by key alone: one key per module
#: per name would let a second, real leak of the same name in the same module hide behind
#: the first. Not keyed by line number, which churns on every edit above it.
ACCEPTED_EMISSIONS: dict[str, tuple[int, str]] = {
    # `raise LeagueMismatch(int(entry_id), claims.league_id)`. `entry_id` matches on the
    # component `entry`; it is the lega's numeric id read out of the storage entry, not
    # the entry, and the comment on the line above says so. The evidence pins the whole
    # call, so carrying the token into that raise changes it and this stops holding.
    "domain/tokens/capture.py:entry_id": (
        1,
        "raise LeagueMismatch(int(entry_id), claims.league_id)",
    ),
    # Both login preflights report which database they could not reach. The DSN is
    # rendered with `hide_password=True`, so the userinfo is already masked before it
    # reaches the message — and that is precisely what the evidence string pins.
    "application/auth_login.py:dsn": (1, "render_as_string(hide_password=True)"),
    "application/fantalab_login.py:dsn": (1, "render_as_string(hide_password=True)"),
    # `_write_session` writes the raw Playwright blob — every lega bearer token in it —
    # to `data/storage_state.json` in the clear. Reported the day `write_text` joined
    # `EMIT_NAMES`, and **a true positive, not noise**: it is the designed session file
    # `auth login` re-reads, pre-existing and outside this file's reach to change. What
    # keeps it off SPEC's Never list is that it cannot be committed, which is no longer
    # taken on trust — see `test_the_session_file_the_login_writes_cannot_be_committed`.
    # The evidence pins the write itself, so writing something else, or somewhere else,
    # is a decision someone has to make again.
    "application/auth_login.py:blob": (1, "path.write_text(json.dumps(blob))"),
}


def _tracked_files() -> list[Path]:
    """What git tracks — the only files that can leak anything by being pushed."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [REPO / line for line in out.stdout.splitlines() if line]


def test_no_jwt_literal_is_tracked_by_git() -> None:
    """Assertion 1. A token that reaches a tracked file is a published token."""
    offenders = []
    for path in _tracked_files():
        if path.resolve() == SELF or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: cannot hold a JWT literal as text
        if JWT_LITERAL.search(text):
            offenders.append(str(path.relative_to(REPO)))

    assert offenders == [], (
        f"a JWT-shaped literal is tracked by git in: {offenders}. Test tokens are "
        "synthesized; a real one must never enter the repository."
    )


def test_the_encryption_key_cannot_be_committed() -> None:
    """Assertion 2. `.env` is ignored, and the example carries no real value."""
    ignored = subprocess.run(["git", "check-ignore", ".env"], cwd=REPO, capture_output=True)
    assert ignored.returncode == 0, ".env is not git-ignored — the key could be committed"

    lines = (REPO / ".env.example").read_text().splitlines()
    key_lines = [ln for ln in lines if ln.startswith("FANTABOT_ENCRYPTION_KEY=")]

    assert key_lines == ["FANTABOT_ENCRYPTION_KEY="], (
        f"`.env.example` must carry an empty right-hand side, found: {key_lines}"
    )


def test_no_command_accepts_a_key_on_argv() -> None:
    """Assertion 3. argv is visible in `ps` and persists in shell history."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text()
        for option in FORBIDDEN_OPTIONS:
            if f'"{option}"' in text or f"'{option}'" in text:
                offenders.append(f"{path.relative_to(PACKAGE)}: {option}")

    assert offenders == [], (
        f"a credential must never be an argv option: {offenders}. Rotation is "
        "re-login; if a key ever has to be passed, it arrives by environment."
    )


def test_the_allowlist_names_files_that_exist() -> None:
    """An entry pointing at nothing allows nothing, and says so to no one.

    All four moved in P12-4 and P12-6, and an allowlist is only ever read when something
    fails -- so a set of paths that had all gone stale would have kept passing.
    """
    missing = sorted(name for name in DECRYPT_ALLOWED if not (PACKAGE / name).is_file())

    assert missing == [], f"the allowlist names files that do not exist: {missing}"


def test_each_half_of_the_allowlist_means_what_it_says() -> None:
    """A live site that stopped decrypting, or a reservation that started.

    Either is a real change worth seeing: the first is an allowance nothing uses, which
    is a hole waiting for whatever lands at that path next; the second is a new
    decryption site that arrived without anyone deciding it should.
    """
    silent = sorted(n for n in DECRYPT_SITES if "decrypt(" not in (PACKAGE / n).read_text())
    woken = sorted(n for n in DECRYPT_RESERVED if "decrypt(" in (PACKAGE / n).read_text())

    assert silent == [], f"listed as decryption sites and do not decrypt: {silent}"
    assert woken == [], (
        f"reserved but now decrypting: {woken}. If that is intended, move it to "
        "DECRYPT_SITES in the same commit and say why in the message."
    )


def test_decrypt_is_confined_to_its_allowed_files() -> None:
    """Assertion 4 — *(deferred, proved red at T13)*.

    Vacuously true until `tokens/crypto.py` exists. It is written now so the
    file cannot be created outside the allowlist without this failing.
    """
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if "decrypt(" in path.read_text()
        and str(path.relative_to(PACKAGE)) not in DECRYPT_ALLOWED
    ]

    assert offenders == [], (
        f"decryption must stay behind the store: {offenders} are outside "
        f"{sorted(DECRYPT_ALLOWED)}"
    )


#: The module holding `config-check`'s **secret set**, and the name it is bound to.
#:
#: Resolved through the import system, like the ones below, and it has now moved twice:
#: W6 took it from `fantabot/cli.py` to `interface/app.py`, and 4.6 took the set itself
#: out of the Typer body into `application/config_report.py`, because the app's System
#: page renders the same report and a second copy of "which fields are secret" is a copy
#: that drifts. **The guard failed red on that move rather than open** — which is the
#: whole design of this file, and the opposite of what happened when `apileague.py` was
#: addressed by path.
CONFIG_REPORT = "fantabot.application.config_report"
SECRET_SET_NAME = "SECRET_FIELDS"

#: The module that still holds `config-check` itself — now a printer, and asserted to
#: have stayed one.
ROOT_APP = "fantabot.interface.app"

#: Modules outside `tokens/` that hold a plaintext token at some point.
#:
#: `login_wait` earns its place by volume rather than by role: it re-reads the browser's
#: storage once a second for up to ten minutes, so a single login puts a credential
#: through it hundreds of times. Its heartbeat must therefore print constant strings and
#: elapsed minutes only — never a length, a prefix, or a boolean derived from a value.
#: `config_report` earns its place by breadth: it is the one module that reads *every*
#: secret in `Settings` — the Fernet key, the lega password, the agent bearer token and
#: the DSN's password — in order to report that they exist. A module that touches all of
#: them is the one where a stray `print` costs the most.
LOOSE_TOKEN_HANDLERS = (
    "fantabot.adapters.http.apileague",
    "fantabot.application.auth_login",
    "fantabot.application.login_wait",
    "fantabot.application.config_report",
    # The five below were absent until 2026-09-24. Every one of them holds a plaintext
    # credential, and the scan named four modules while nine qualified — so the guard
    # read as "every loose handler is checked" and meant "four of them are". Judged one
    # by one against the criteria above, they earn their places by *role*, which is the
    # first criterion and the one the original four's comment left implicit:
    #
    # `fantalab_login` is the structural twin of `auth_login`, which is listed. Phase 5
    # added the second store and updated `DECRYPT_SITES` -- its comment records that
    # decision -- and stopped one file short.
    "fantabot.application.fantalab_login",
    # `rtdb.place_raise` puts a token in a **query string** (`params = {"auth": token}`).
    # A URL carrying a credential is the leak shape with the longest tail: it reaches
    # proxies, access logs and httpx tracebacks that a header never does. The module is
    # aware -- `BidOutcome` deliberately holds neither the URL nor the token -- and
    # nothing enforced that on the next edit.
    "fantabot.adapters.http.fantalab.rtdb",
    # `rest.bearer_from` -> `_headers(token)` -> `f"Bearer {token}"`: the one module that
    # turns the stored session into a header, and whose docstrings promise the bearer
    # "never enters the caller's frame".
    "fantabot.adapters.http.fantalab.rest",
    # `harvest/client.py` holds `self._token` for the life of the client and interpolates
    # it into an f-string. Its own docstring claims "only this module ever names a token"
    # -- which is precisely the claim this walk exists to enforce, and did not.
    "fantabot.adapters.http.harvest.client",
    # `capture.read_storage_state` returns the raw browser blob holding all three
    # FantaLab JWTs plus every lega cookie. By breadth, like `config_report`: it is the
    # single value from which every other credential in this list is derived.
    "fantabot.adapters.browser.capture",
)


def _scanned_sources() -> list[Path]:
    """The modules that will handle a plaintext token.

    Both halves of `tokens/`, asked for by name. It was `PACKAGE / "tokens"` guarded by
    `if ... is_dir() else []`, which fails open: after P12-4 split the package across two
    layers the guard silently reduced this scan from nine modules to two, and every
    secrecy assertion below still reported green.
    """
    paths = [p for directory in pkgs("tokens") for p in directory.rglob("*.py")]
    # Named as modules and resolved through the import system. They were
    # `PACKAGE / "apileague.py"` and `PACKAGE / "login.py"`, filtered by `is_file()` --
    # so when `apileague.py` moved to `adapters/http/` it was dropped from every
    # assertion in this file without one of them going red. The floor below counts token
    # modules, which alone cleared it.
    paths += [module_file(m) for m in LOOSE_TOKEN_HANDLERS]
    missing = [p for p in paths if not p.is_file()]
    assert not missing, f"named but not found: {missing}"
    return paths


def _base_identifier(node: ast.expr) -> str | None:
    """`token` from `token`, `token[:8]`, `entry.token`, `self.ciphertext`."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript | ast.Starred):
            node = node.value
            continue
        return None


#: What counts as an emit: **a call whose output can reach a human or a file.**
#:
#: Written as an answer to that question rather than as a list of the verbs someone
#: happened to think of, because twice now this set has been widened by vocabulary and
#: the *shape* underneath it left alone. The families, and what each one is doing here:
#:
#: * the terminal — `print`, `typer.echo`/`secho`, `pprint.pprint`, and rich's
#:   `console.log`, which the `log`-ish receiver rule below does **not** reach (`log` is
#:   the verb, `console` the receiver, and neither half matched);
#: * a prompt — `input`, `typer.prompt`, `typer.confirm`. The prompt string is rendered
#:   before the answer is read, so `typer.prompt(f"paste over {token}")` is a print;
#: * stderr and the traceback — `sys.exit(str)`, which writes its message out before the
#:   interpreter stops, and `traceback.print_exception`. `raise SystemExit(...)` was
#:   already caught by the `Raise` branch and the call form was not, which is how a
#:   credential could leave through the shortest line anyone writes;
#: * warnings — `warnings.warn`, `warnings.showwarning`, `logger.warning`, `syslog`;
#: * a file — `write`/`writelines`/`write_text`/`write_bytes`, `json.dump` and friends,
#:   and `csv`'s `writerow`/`writerows`. A credential written to a file in the clear is
#:   the store's whole reason for existing, inverted.
#:
#: Measured over the nineteen scanned modules the day the set reached this size: zero
#: new reports against `ACCEPTED_EMISSIONS`, so none of them needed an exemption. The
#: widening is proved a superset by enumeration in
#: `test_the_emit_test_is_a_superset_of_the_shape_it_replaced`, not by this comment.
EMIT_NAMES = frozenset(
    {
        # the terminal
        "print",
        "pprint",
        "echo",
        "secho",
        "log",
        # a prompt
        "input",
        "prompt",
        "confirm",
        # stderr, and the traceback
        "exit",
        "print_exception",
        # warnings
        "warn",
        "warning",
        "showwarning",
        "syslog",
        # a file
        "write",
        "writelines",
        "write_text",
        "write_bytes",
        "dump",
        "writerow",
        "writerows",
    }
)


def _receiver_chain(node: ast.expr) -> list[str]:
    """Every identifier along the receiver of a call, nearest first.

    `_base_identifier` with two things added, and it exists because the emit site is
    the half of this guard that keeps being widened by vocabulary instead of by shape.

    * it **descends through `ast.Call`**. `logging.getLogger(__name__).error(token)` is
      how the stdlib documents a logger, and it was measured surviving: the receiver is
      a `Call`, `_base_identifier` returns `None` for one, and the walk that calls
      itself the leak guard could not see a call reaching a log. The control —
      `log = logging.getLogger(__name__)` then `log.error(token)`, which is how this
      repo actually spells it (`adapters/agent/runner.py:28`) — was caught all along,
      so the difference was never the argument and always the receiver;
    * it returns the **whole chain** rather than the nearest name, which reaches
      `logger.bind(lid=1).info(token)`: `bind` is the nearest identifier and says
      nothing, `logger` is one step further and says everything.

    The `log`-ish test on these is a substring, so a receiver named `catalog` or
    `dialog` reads as a logger and its calls are reported — as does `math.log`, which
    `EMIT_NAMES` claims by its verb. Measured over the whole repository: five call
    spellings gained that are not emits, none of them in a scanned module, so the walk
    reports the same four emissions as before. That was already true of the nearest
    name and is now true one step deeper; it fails **red**, which is the direction this
    file is allowed to be wrong in.
    """
    found: list[str] = []
    while True:
        if isinstance(node, ast.Name):
            found.append(node.id)
            return found
        if isinstance(node, ast.Attribute):
            found.append(node.attr)
            node = node.value
            continue
        if isinstance(node, ast.Subscript | ast.Starred):
            node = node.value
            continue
        if isinstance(node, ast.Call):
            node = node.func
            continue
        return found


def _emits(node: ast.Call) -> bool:
    """`print(...)`, `console.print(...)`, `logger.info(...)` and friends.

    Four shapes can denote an emit, and all four are answered here: a bare name
    (`print`), an attribute of a name (`console.print`, `log.error`), an attribute of
    an attribute (`self.log.info`) and an attribute of a **call**
    (`logging.getLogger(__name__).error`) — the last of which this walk could not see
    until 2026-09-24.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in EMIT_NAMES
    if isinstance(func, ast.Attribute):
        if func.attr in EMIT_NAMES:
            return True
        return any("log" in name.lower() for name in _receiver_chain(func.value))
    return False


def _names_a_secret(name: str | None) -> bool:
    """Does this identifier name a credential? The **whole** of it, then its components.

    Both clauses, and the order is only for reading — the rule is the union:

    * the whole identifier, with a leading or trailing underscore stripped, because the
      private underscore is a visibility convention and not a different value. This is
      the clause that carries `storage_state`, the only multi-word entry in
      `SECRET_NAMES` and the one a components-only rule blinded the guard to;
    * any underscore-separated component, because a credential's real field name is
      compound: `refresh_token`, `id_token`, `access_token`, `self._token`.

    Both clauses are applied to the **case-folded** identifier. UPPER_SNAKE is Python's
    spelling for a module constant and `SECRET_NAMES` is written in lower case, so
    until 2026-09-24 the matcher answered `False` to `TOKEN`, `BEARER_TOKEN` and
    `APP_KEY` — the last of which is a real constant in a scanned module
    (`adapters/http/apileague.py:55`). Nothing in the source scan could see that either:
    the vocabulary the set claimed was simply unreachable in the case code writes it,
    and `print(BEARER_TOKEN)` inside `domain/tokens/crypto.py` was measured surviving.

    Each component is also tried in the **singular**, because a collection of credentials
    is still credentials. `SECRET_NAMES` grew four plurals by hand — `cookies`,
    `credentials`, `headers`, `params` — and nothing related them to the seventeen
    singular entries beside them, so the set's own vocabulary was unreachable the moment
    code named more than one of something. Measured 2026-09-24 in
    `domain/tokens/capture.py`, one helper, one emit, only the parameter name changing:

        def _describe(entry: object) -> None:    print(f"leagues: {entry}")    # CAUGHT
        def _describe(entries: object) -> None:  print(f"leagues: {entries}")  # SURVIVED

    and the same pair for `token`/`tokens`, `key`/`keys`, `session`/`sessions`. In a
    repository with a `domain/tokens/` package, `tokens` is not an exotic spelling. The
    four hand-patched plurals are kept rather than removed: they match directly *and*
    singularise into the set, so the rule and the list agree instead of one masking the
    other, and deleting one would not change an answer.

    Strictly wider than any of the four. Every clause is a disjunct over the same
    identifier, so each one can only add spellings — and every entry of `SECRET_NAMES` is
    lower case with no leading or trailing underscore, so `name in SECRET_NAMES` implies
    `name.strip("_").lower() in SECRET_NAMES`. `test_every_secret_name_matches_itself` and
    `test_the_matcher_is_a_superset_of_both_forms_it_replaced` hold that shape down —
    the second by enumeration, which is the only form of this claim worth making.
    """
    if name is None:
        return False
    stripped = name.strip("_").lower()
    if stripped in SECRET_NAMES:
        return True
    components = set(stripped.split("_"))
    return bool((components | {_singular(c) for c in components}) & SECRET_NAMES)


def _singular(word: str) -> str:
    """`word` with one English plural suffix removed, or `word` unchanged.

    Deliberately three rules and not a stemmer: the question is only whether an
    identifier component is the plural of a word in `SECRET_NAMES`, and every entry there
    is a short common noun. A stemmer would also fold unrelated words together, which in
    a guard means false positives an author has to argue with — and an assertion people
    argue with is one they eventually weaken.
    """
    if word.endswith("ies") and len(word) > 3:
        return f"{word[:-3]}y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _secret_name(node: ast.expr) -> str | None:
    """The identifier behind `node` when it names a credential, else `None`."""
    name = _base_identifier(node)
    return name if _names_a_secret(name) else None


def _secret_arguments(node: ast.expr) -> list[str]:
    """Every credential-shaped thing anywhere inside one emitted expression.

    A walk of the **whole subtree**, not a peel down to the outermost identifier. What
    the guard is trying to assert is "no expression that can carry a credential reaches
    a print, a log or a raise", and an expression carries one wherever it mentions one —
    not only at its root. `_base_identifier` gave up on every shape that is not a name
    with decorations on it, and each of those was measured surviving in a module this
    file had just admitted for holding a plaintext credential:

    * `print("Bearer " + token)` — a `BinOp`, the exact shape a debug line takes; also
      `"%s" % token`;
    * `print({"auth": token})`, `print([token])` — a collection literal holding it;
    * `print(f"PATCH {url} params={params}")` — an alias named after what it is;
    * `print(str(token))`, `print(f"{fmt(token)}")` — a credential passed through a call.

    `ast.walk` sees all of them, and it is a strict superset of the peel it replaced:
    for a `Name` it yields that name; for `a.b` it yields the `Attribute` (whose `attr`
    is `b`, what the peel returned) and the `Name` `a` as well; for `token[:8]` and
    `*token` it reaches the inner `Name`. Everything the peel answered `None` to is
    where it gains.

    The third clause is the call-return ratchet — see `CREDENTIAL_RETURNING`, and the
    honest statement of what it does not close.

    Deduplicated within one expression, because `entry.token` legitimately reports twice
    (`entry`, `token`) and a count is part of what `ACCEPTED_EMISSIONS` pins.
    """
    found: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and _names_a_secret(sub.id):
            found.append(sub.id)
        elif isinstance(sub, ast.Attribute) and _names_a_secret(sub.attr):
            found.append(sub.attr)
        elif isinstance(sub, ast.Call):
            callee = _base_identifier(sub.func)
            if callee is not None and callee.strip("_") in CREDENTIAL_RETURNING:
                found.append(f"{callee}()")
    return list(dict.fromkeys(found))


def _emitted_arguments(node: ast.AST) -> list[ast.expr] | None:
    """What `node` sends to a print, a log, a traceback or a file — or `None`.

    `Raise` reads `node.exc.keywords` as well as `node.exc.args`. It did not, while the
    `Call` branch two lines above it always had, so the *same* expression was caught in
    `print(x=token)` and missed in `raise LoginAborted(message=token, code=1)` — which
    is valid Python against the live `LoginAborted.__init__(self, message,
    code=EXIT_PREFLIGHT)` and was measured surviving.

    The exception class itself (`node.exc.func`) is deliberately not walked: it is the
    name of a type, never a value, and walking it would report `raise TokenError(...)`
    for the word in its own name.

    `Assert` is new. An assertion message is a traceback line with a `-O` switch in
    front of it, and the reason `Raise` is in this walk applies to it unchanged.
    """
    if isinstance(node, ast.Call) and _emits(node):
        return [*node.args, *(kw.value for kw in node.keywords)]
    if isinstance(node, ast.Raise) and node.exc is not None:
        if isinstance(node.exc, ast.Call):
            return [*node.exc.args, *(kw.value for kw in node.exc.keywords)]
        return [node.exc]
    if isinstance(node, ast.Assert) and node.msg is not None:
        return [node.msg]
    return None


def _emissions(tree: ast.AST) -> list[tuple[int, str]]:
    """`(line, identifier)` for every credential-shaped thing this tree emits."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        args = _emitted_arguments(node)
        if args is None:
            continue
        for arg in args:
            found.extend((node.lineno, name) for name in _secret_arguments(arg))
    return found


def test_no_credential_reaches_a_print_a_log_or_a_raise() -> None:
    """Assertion 5 — *(deferred, proved red at T13)*.

    An AST walk, not a regex, because `console.print(f"{lid}: {token[:8]}…")`
    must fail while `console.print(f"{n} tokens stored")` — which is SPEC's own
    example output — must pass. A substring scan cannot tell those apart.

    `Raise` is included alongside prints and logs: an exception message is a log
    line that also gets a traceback, and SPEC's `decode_claims` is explicit that
    "a truncated or wrong-format credential in a traceback is still a credential".

    The result is compared **by count** against `ACCEPTED_EMISSIONS` rather than asserted
    empty. Three live reports are not leaks and each carries its own evidence; an empty
    assertion would have to be bought either by weakening the matcher — which is what
    this round is about — or by a blanket skip that outlives its reason.
    """
    reported: Counter[str] = Counter()
    where: dict[str, list[str]] = {}
    for path in sorted(_scanned_sources()):
        module = str(path.relative_to(PACKAGE))
        for line, name in _emissions(ast.parse(path.read_text())):
            key = f"{module}:{name}"
            reported[key] += 1
            where.setdefault(key, []).append(f"{module}:{line}")

    expected = {key: count for key, (count, _) in ACCEPTED_EMISSIONS.items()}
    unexpected = sorted(
        f"{key} x{n} at {where[key]}" for key, n in reported.items() if expected.get(key) != n
    )
    assert dict(reported) == expected, (
        "a credential must not reach a print, a log, an assertion message or a "
        f"traceback. Unaccounted: {unexpected}. Accounted for and now absent: "
        f"{sorted(set(expected) - set(reported))}"
    )

    stale = []
    for key, (_, evidence) in ACCEPTED_EMISSIONS.items():
        module, _, _name = key.rpartition(":")
        if evidence not in (PACKAGE / module).read_text():
            stale.append(f"{key}: {evidence!r} is no longer in {module}")
    assert stale == [], (
        f"an entry in ACCEPTED_EMISSIONS outlived the reason it was safe: {stale}. "
        "The evidence is the exemption; without it the emission is a leak again."
    )


def test_every_secret_name_matches_itself() -> None:
    """The matcher's vocabulary is only as large as what the matcher can see.

    The direct pin for the 2026-09-24 regression, and the assertion that makes a source
    mutation unnecessary to notice it. `_secret_name` was rewritten to test an
    identifier's *components* — a real widening for `refresh_token` and `self._token`,
    and a silent narrowing for `storage_state`, which splits into two words that are not
    in the set. Effective vocabulary went from eleven to ten, and the tenth was the entry
    naming the raw browser blob holding all three FantaLab JWTs.

    Nothing in the source scan could see that: a vocabulary word that matches nothing
    reports nothing, and the scan's assertion is that it reports nothing.

    **In every case code writes it**, which this test did not ask until 2026-09-24 and
    is the second regression of exactly this kind. The set is written in lower case and
    the matcher did not fold case, so the vocabulary was unreachable from UPPER_SNAKE —
    Python's own spelling for a module constant, and how a scanned module spells
    `APP_KEY`. The corpus below is therefore each entry in the three cases an identifier
    is actually written in, not just the one the set happens to be typed in.

    `str.title()` is deliberately not here: `StorageState` has no underscore to split on
    and does not fold to a word in the set, so CamelCase is **not** covered and this
    file does not claim it is.
    """
    corpus = sorted(
        {*SECRET_NAMES, *(n.upper() for n in SECRET_NAMES), *(n.capitalize() for n in SECRET_NAMES)}
    )
    unmatched = sorted(
        name for name in corpus if _secret_name(ast.parse(name, mode="eval").body) != name
    )

    assert unmatched == [], (
        f"SECRET_NAMES contains {unmatched}, and the matcher does not match them — so "
        "the set claims a vocabulary the guard does not have. A multi-word entry and an "
        "UPPER_SNAKE constant are the two shapes that break first."
    )


def test_the_matcher_is_a_superset_of_both_forms_it_replaced() -> None:
    """Every spelling either earlier version caught, enumerated and still caught.

    The claim this round exists to stop anyone making without checking. Version one was
    exact membership; version two was a component test, reported as a "strict superset"
    and measurably not one — it dropped `storage_state`, the only entry with a component
    in it. Neither is a superset of the other, so the two are written out here and run
    against a corpus of spellings rather than compared from memory.

    The second half matters as much as the first: a matcher that says yes to everything
    is also a superset. So the delta is pinned exactly — two spellings gained, both of
    them `storage_state` wearing a private underscore — and the corpus carries
    non-secrets, including the two components `storage_state` decomposes into, which
    must stay unmatched or the widening would be the blanket it replaced.
    """

    def version_one(name: str) -> bool:
        """`name in SECRET_NAMES` — exact membership, the shape before 2026-09-24."""
        return name in SECRET_NAMES

    def version_two(name: str) -> bool:
        """The component test that replaced it, and lost `storage_state` doing so."""
        return bool(set(name.strip("_").split("_")) & SECRET_NAMES)

    def version_three(name: str) -> bool:
        """Their union, case-blind — the shape between the two rounds of this review.

        A real widening over both, and a silent narrowing of the *vocabulary*: nothing
        written in the case a module constant is written in could match it.
        """
        stripped = name.strip("_")
        return stripped in SECRET_NAMES or bool(set(stripped.split("_")) & SECRET_NAMES)

    lower = {
        *SECRET_NAMES,
        *(f"_{n}" for n in SECRET_NAMES),
        *(f"{n}_" for n in SECRET_NAMES),
        *(f"refresh_{n}" for n in SECRET_NAMES),
        *(f"{n}_value" for n in SECRET_NAMES),
        # Not secrets. `state` and `storage` are here on purpose: they are what
        # `storage_state` splits into, and matching either would be a vocabulary
        # this set never claimed.
        "state",
        "storage",
        "league_id",
        "fingerprint",
        "claims",
        "n",
        "lid",
        "moment",
        "report",
        "response",
        "row",
        "user_id",
    }
    # The same corpus in the two other cases an identifier is written in. Until these
    # were here, every spelling this test knew about was lower case, so a matcher that
    # could not see an UPPER_SNAKE constant passed it — and the source scan could not
    # see that either. `APP_KEY` is not a synthetic case: it is a live constant in
    # `adapters/http/apileague.py`, and it is a credential.
    corpus = sorted(lower | {s.upper() for s in lower} | {s.capitalize() for s in lower})

    lost = sorted(
        s
        for s in corpus
        if (version_one(s) or version_two(s) or version_three(s)) and not _names_a_secret(s)
    )
    assert lost == [], (
        f"the matcher lost {lost}, which an earlier version caught. A widening that "
        "narrows is what produced this round, twice."
    )

    # Split the delta by clause, because a single literal list of everything gained
    # would be long enough to stop being read — and being unread is how the lower-case
    # corpus survived a round.
    gained_whole = sorted(
        s for s in corpus if s == s.lower() and _names_a_secret(s) and not version_two(s)
    )
    assert gained_whole == ["_storage_state", "storage_state", "storage_state_"], (
        f"the delta over the component test, at equal case, is {gained_whole} — which "
        "is not the whole-identifier clause and nothing else. Anything more is a blanket."
    )

    mismatched = sorted(
        s for s in corpus if s != s.lower() and _names_a_secret(s) != _names_a_secret(s.lower())
    )
    assert mismatched == [], (
        f"{mismatched} are matched differently from their own lower-case spelling. The "
        "case fold may only add the spellings that already matched in another case; "
        "matching more than that would be the blanket, and matching fewer is the "
        "narrowing this round found."
    )
    # And the fold is not vacuous: it is what carries an UPPER_SNAKE constant.
    assert _names_a_secret("APP_KEY") and _names_a_secret("BEARER_TOKEN")
    assert not _names_a_secret("LEAGUE_ID") and not _names_a_secret("STORAGE")


#: `_emits` as it stood before 2026-09-24, kept as a literal so the superset claim below
#: is an enumeration rather than a memory. Two rounds of this review have now shipped a
#: "widening" that narrowed something, and both were found by writing the old version
#: out and running both.
PREVIOUS_EMIT_NAMES = frozenset(
    {"print", "echo", "warn", "warning", "write", "write_text", "write_bytes"}
)

#: How an emit can be *denoted*, and what each spelling is doing here. The value is why
#: it must (or must not) count, and it is printed when the assertion fails.
EMIT_SPELLINGS = {
    "print(x)": "a bare name",
    "pprint.pprint(x)": "...and a stdlib one that is not `print`",
    "console.print(x)": "an attribute of a name",
    "self.console.print(x)": "an attribute of an attribute",
    "log.error(x)": "the `log`-ish receiver, spelled the way this repo spells it",
    "self._logger.warning(x)": "...private, and one level deeper",
    "logging.getLogger(__name__).error(x)": "an attribute of a CALL — the stdlib's own",
    "get_logger().info(x)": "...the same shape with the module elided",
    "logger.bind(lid=1).info(x)": "...and with the logger one step further back",
    "sys.exit(x)": "stderr, and the shortest line anyone writes",
    "traceback.print_exception(x)": "the traceback, printed on purpose",
    "typer.echo(x)": "the CLI's print",
    "typer.secho(x)": "...styled, which is a different name for the same write",
    "typer.prompt(x)": "a prompt renders before it reads",
    "typer.confirm(x)": "...so does this one",
    "input(x)": "...and the builtin",
    "console.log(x)": "rich's Console.log: `log` is the verb, not the receiver",
    "warnings.warn(x)": "a warning reaches stderr",
    "warnings.showwarning(x)": "...and so does the hook under it",
    "syslog.syslog(x)": "the system log",
    "sys.stdout.write(x)": "written, not printed",
    "sys.stdout.writelines(x)": "...in the list form",
    "path.write_text(x)": "a file, in the clear",
    "path.write_bytes(x)": "...in bytes",
    "json.dump(x, fh)": "...through a serializer",
    "writer.writerow(x)": "...through csv",
    "writer.writerows(x)": "...and its plural",
}

#: Calls that are **not** emits. The half that keeps a widening from being a blanket:
#: every verb added above has to leave these alone.
NON_EMIT_SPELLINGS = {
    "store.save(x)": "persisting a credential is the point of the store",
    "cipher.encrypt(x)": "encrypting one is not emitting it",
    "TokenCipher(x)": "a constructor",
    "_headers(x)": "a helper that returns one",
    "httpx.post(url, json=x)": "sending it to the API it authenticates",
    "self.fetch(x)": "an ordinary method on an ordinary receiver",
    "dict(x)": "a builtin that is not an emit",
}


def test_the_emit_test_is_a_superset_of_the_shape_it_replaced() -> None:
    """Every call the previous `_emits` treated as an emit, enumerated and still one.

    The emit **site** is the half of this guard that has been widened by vocabulary
    twice while its shape was left alone, and the cost was measured: with
    `token = store.load_plaintext(...)` one line above it,
    `logging.getLogger(__name__).error("auth failed: %s", token)` survived the whole
    walk, while the same verb on a bound logger — `log.error("auth failed: %s", token)`,
    the way `adapters/agent/runner.py:28` spells it — was caught. The argument was a
    bare `ast.Name`, the simplest shape there is. The difference was the receiver.

    So the claim is checked the way the vocabulary's is: both versions are run over a
    corpus of spellings and the two sets compared. The second assertion matters as much
    as the first — a function returning `True` for everything is also a superset.
    """
    corpus = {**EMIT_SPELLINGS, **NON_EMIT_SPELLINGS}

    def previous_emits(node: ast.Call) -> bool:
        """`_emits` before this round: no `Call` in the receiver, nearest name only."""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in PREVIOUS_EMIT_NAMES
        if isinstance(func, ast.Attribute):
            if func.attr in PREVIOUS_EMIT_NAMES:
                return True
            base = _base_identifier(func.value)
            return base is not None and ("log" in base.lower())
        return False

    def call(src: str) -> ast.Call:
        node = ast.parse(src, mode="eval").body
        assert isinstance(node, ast.Call), src
        return node

    lost = sorted(src for src in corpus if previous_emits(call(src)) and not _emits(call(src)))
    assert lost == [], (
        f"the emit test lost {lost}, which the previous version treated as an emit. "
        "A widening that narrows is what produced this round, twice."
    )

    missed = sorted(src for src in EMIT_SPELLINGS if not _emits(call(src)))
    assert missed == [], "these reach a human or a file and the walk does not see it: " + str(
        [f"{src}  ({EMIT_SPELLINGS[src]})" for src in missed]
    )

    spurious = sorted(src for src in NON_EMIT_SPELLINGS if _emits(call(src)))
    assert spurious == [], "these are not emits and the walk calls them one: " + str(
        [f"{src}  ({NON_EMIT_SPELLINGS[src]})" for src in spurious]
    )

    # The delta, pinned. Not a count: the whole point of the two rounds behind this file
    # is that "wider" was asserted and not enumerated.
    gained = sorted(src for src in corpus if _emits(call(src)) and not previous_emits(call(src)))
    assert gained == sorted(
        src for src in EMIT_SPELLINGS if not previous_emits(call(src))
    ), f"the delta reaches outside the emit corpus: {gained}"
    assert "logging.getLogger(__name__).error(x)" in gained, (
        "the shape this round was opened by is not in the delta, so either it was "
        "already covered or the corpus no longer contains it."
    )


def test_the_walk_sees_every_shape_that_can_carry_a_credential() -> None:
    """The class, not the five examples it was reported as.

    Each line below was written as a *variant* of a reported leak rather than the leak
    itself, because closing the literal example is what produced this round. A guard
    that is only as wide as its demo case catches the demo case.
    """
    must_flag = {
        'print("Bearer " + token)': "BinOp — concatenation, the shape a debug line takes",
        'print("%s" % plaintext)': "BinOp again, with the old formatting operator",
        'print({"auth": token})': "a dict literal holding it",
        'print([token, other])': "a list literal holding it",
        'print((token,))': "a tuple holding it",
        'print(f"PATCH {url} params={params}")': "an alias named after what it is",
        'print(storage_state)': "the one multi-word name in SECRET_NAMES",
        'print(f"{parsed.storage_state}")': "...reached through an attribute",
        'print(f"dsn password: {url.password}")': "a word the vocabulary did not have",
        'print(session.access_token)': "a compound field name",
        'print(self._token)': "a private one",
        'raise LoginAborted(message=blob, code=1)': "a keyword argument to a raise",
        'raise LoginAborted(f"{read_storage_state(ctx)}")': "a call inside one",
        'assert ok, f"{token}"': "an assertion message is a traceback line",
        "logger.info('x', extra={'jwt': entry.payload})": "a keyword to a log call",
        "console.print(str(ciphertext))": "wrapped in a call",
        "sys.stdout.write(plaintext)": "written, not printed",
        "typer.echo(headers)": "echoed",
        "warnings.warn(f'{cookies}')": "warned",
        # The three below were measured surviving after the round that added the four
        # verbs above, each in a module this file already scans. Two of them are the
        # emit site rather than the argument, which is the half the vocabulary round
        # left alone.
        'logging.getLogger(__name__).error("auth failed: %s", token)': (
            "a logger resolved through a call — the stdlib's own spelling"
        ),
        "logger.bind(lid=1).info(token)": "...and one behind a builder call",
        "print(BEARER_TOKEN)": "an UPPER_SNAKE constant, which the set could not see",
        "print(APP_KEY)": "...and a live one, in a module this file scans",
        'sys.exit(f"stuck: {parsed.id_token}")': "sys.exit(str) writes to stderr",
        "console.log(session)": "rich's Console.log, where `log` is the verb",
        "typer.secho(plaintext)": "styled echo is still an echo",
        "typer.prompt(f'paste over {token}')": "a prompt renders before it reads",
        "sys.stdout.writelines([ciphertext])": "the list form of write",
        "json.dump(blob, fh)": "a credential serialized into a file",
        "writer.writerow([cookies])": "...and one written through csv",
        "print(bearer_from(store, user_id))": "a call whose return is a credential",
        "print(read_storage_state(ctx))": "...and one whose name does not say so",
        "print(parse_fantalab_storage(state))": "...nor this one",
        'raise LoginAborted(message=f"{read_state()}", code=1)': "both at once",
    }
    must_not_flag = {
        'print(f"{n} tokens stored")': "SPEC's own permitted output",
        'print("nothing was opened and nothing was written")': "a constant",
        'print(f"{league_id} verified")': "an id",
        "print(fingerprint)": "a fingerprint is printable on purpose, and is why",
        "print(f'{claims.league_id} {claims.exp}')": "claims are not the token",
        "store.save(token)": "not an emit",
        "cipher = TokenCipher(key)": "not an emit either",
        "return _headers(token)": "...nor this",
        "httpx.post(url, json={'auth': token})": "sending it where it belongs",
        "print(LEAGUE_ID)": "an UPPER_SNAKE constant that is not a credential",
        "print(f'{STORAGE} and {STATE}')": "...nor are the two words `storage_state` splits into",
    }

    missed = sorted(src for src in must_flag if not _emissions(ast.parse(src)))
    assert missed == [], "these carry a credential and the walk does not see it: " + str(
        [f"{src}  ({must_flag[src]})" for src in missed]
    )

    spurious = sorted(src for src in must_not_flag if _emissions(ast.parse(src)))
    assert spurious == [], "these are not leaks and the walk reports them: " + str(
        [f"{src}  ({must_not_flag[src]})" for src in spurious]
    )


def test_the_credential_returning_ratchet_is_live_and_minimal() -> None:
    """`CREDENTIAL_RETURNING` is the hole this file does not claim to have closed.

    A call's return type is not in the tree, so the shape is pinned by a list rather
    than decided. A list is only worth having while it is true, so both halves are
    checked: every entry still names a function defined in a scanned module — a rename or
    a deletion turns this red and the entry has to be edited with the code — and no entry
    duplicates a word already in `SECRET_NAMES`, because two gates on one question make
    the first unobservable and a mutation of it reports SURVIVED for the wrong reason.
    """
    bound: set[str] = set()
    for path in _scanned_sources():
        for node in ast.walk(ast.parse(path.read_text())):
            # A `def`, a parameter, or an assignment target. Not `def` alone: a
            # credential can arrive through an injected callable — `read_state` is a
            # parameter of `fantalab_login.run` and never a function in this tree — and
            # a liveness check that only knows about `def` would reject the entry that
            # pins it, which is how a ratchet gets deleted for being "wrong".
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                bound.add(node.name.strip("_"))
            elif isinstance(node, ast.arg):
                bound.add(node.arg.strip("_"))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id.strip("_"))

    dead = sorted(CREDENTIAL_RETURNING - bound)
    assert dead == [], (
        f"CREDENTIAL_RETURNING names {dead}, which no scanned module binds. A ratchet "
        "entry that matches nothing allows everything at whatever lands there next."
    )

    redundant = sorted(name for name in CREDENTIAL_RETURNING if _names_a_secret(name))
    assert redundant == [], (
        f"{redundant} are already caught by SECRET_NAMES. Listing them twice means a "
        "mutation of either gate is hidden by the other."
    )


def test_the_rename_hole_is_open_and_says_so() -> None:
    """The second thing this file does not claim to have closed, written down.

    `CREDENTIAL_RETURNING` pins the call-return hole. This pins the other one: a
    credential assigned to a name that does not look like one is invisible from the
    emit site, because deciding it needs data flow and this walk is one pass over the
    syntax. `x = token` then `print(x)` reports nothing, and no vocabulary can fix it —
    the leak is at a line where no word in `SECRET_NAMES` appears.

    It is stated here rather than left in prose so that closing it is a **red test and
    an edit**, the same contract every other ratchet in this file has. Two rounds of
    review have now turned on the difference between what this guard covers and what it
    was described as covering.

    The second half is the anti-vacuity clause, and it is not decoration: a walk that
    stopped reporting anything at all would satisfy the first half on its own. That is
    exactly how three tests elsewhere in this repository went vacuous while passing.
    """
    open_shapes = {
        "x = token\nprint(x)": "renamed to a name that says nothing",
        "copy = plaintext\nlogger.info(copy)": "...and emitted through a log",
        "v = entry.payload\nraise LoginAborted(v)": "...and through a traceback",
    }
    still_open = sorted(src for src in open_shapes if not _emissions(ast.parse(src)))
    assert still_open == sorted(open_shapes), (
        "the rename hole is closed for "
        f"{sorted(set(open_shapes) - set(still_open))} — which is good news and makes "
        "this ratchet false. Delete the entry and say so where the file claims what it "
        "does not cover."
    )

    # Without this, the assertion above passes for a walk that reports nothing at all.
    witness = {
        "print(token)": "the same value, not renamed",
        "logger.info(plaintext)": "the same log",
        "raise LoginAborted(entry.payload)": "the same traceback",
    }
    blind = sorted(src for src in witness if not _emissions(ast.parse(src)))
    assert blind == [], (
        f"the walk reports nothing for {blind}, so the ratchet above is measuring a "
        "dead walk rather than a known hole."
    )


def test_the_session_file_the_login_writes_cannot_be_committed() -> None:
    """The one thing standing between `data/storage_state.json` and a push.

    `auth_login._write_session` writes the raw Playwright blob — every lega bearer token
    in it — to this path in the clear, and it is the one accepted entry in
    `ACCEPTED_EMISSIONS` that is a real exposure rather than a masked or non-credential
    value. It is accepted because the file is a local artefact the login re-reads and
    because it cannot be committed; the second half of that sentence was taken on trust
    until 2026-09-24 and is now read out of git.

    The same argument as assertion 2 for the Fernet key, applied to the value the key
    exists to protect. Asked of the **configured** default rather than a literal, so
    moving it out of `data/` fails here instead of quietly.
    """
    from fantabot.config import settings

    path = settings.fantabot_storage_state
    relative = path if not path.is_absolute() else path.relative_to(REPO)
    ignored = subprocess.run(
        ["git", "check-ignore", str(relative)], cwd=REPO, capture_output=True
    )

    assert ignored.returncode == 0, (
        f"{relative} is not git-ignored, and `auth login` writes every lega bearer "
        "token into it in the clear. A JWT literal in a tracked file is assertion 1's "
        "whole subject."
    )


def test_config_check_excludes_the_encryption_key() -> None:
    """Assertion 6, read out of the source rather than the output.

    An output-only assertion passes vacuously on a machine with no key set — which is
    every CI machine, and the one place this would matter least to catch. So the
    exclude-set literal itself is parsed, now out of `application/config_report.py`.

    Every string constant in the assignment's subtree is collected rather than its direct
    `elts`, because the set is a `frozenset({...})` — a `Call` wrapping the set, whose
    `elts` is empty. Reading only the direct children would have found nothing and
    reported the key as unexcluded, which fails closed but for the wrong reason and would
    have been repaired by weakening the assertion.
    """
    tree = ast.parse(module_file(CONFIG_REPORT).read_text())
    excluded = {
        leaf.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == SECRET_SET_NAME for t in node.targets)
        for leaf in ast.walk(node.value)
        if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)
    }

    assert "fantabot_encryption_key" in excluded, (
        f"{CONFIG_REPORT}'s {SECRET_SET_NAME} is {sorted(excluded)} — without the "
        "key in it, `model_dump` prints the key into every cron log. "
        "`Field(repr=False)` does not suppress `model_dump`."
    )


def test_the_typer_body_keeps_no_exclude_set_of_its_own() -> None:
    """The lift's own guarantee, and the only thing that keeps the test above honest.

    Nothing stops `config-check` from growing a second exclude set beside the printer;
    the assertion above would still pass, reading the one in `config_report`, while the
    command printed from a stale copy. That is the shape the T-spine rule exists for —
    "a decision the app cannot call is a decision the app reimplements".
    """
    tree = ast.parse(module_file(ROOT_APP).read_text())
    rebound = [
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"secrets", SECRET_SET_NAME}
    ]

    assert rebound == [], (
        f"{ROOT_APP} binds {rebound} — `config-check` is a printer, and which fields are "
        f"secret is {CONFIG_REPORT}'s to say. Two copies drift, and the one that drifts "
        "is the one that prints a credential."
    )


def test_the_league_token_repr_leaks_neither_plaintext_nor_ciphertext() -> None:
    """A repr reaches tracebacks, pytest output and cron logs."""

    import _tokens
    from cryptography.fernet import Fernet

    from fantabot.adapters.persistence.models.tokens import LeagueToken
    from fantabot.domain.tokens.crypto import TokenCipher

    plaintext = _tokens.make_token(l_id=_tokens.LEGA_MANTRA)
    cipher = TokenCipher(Fernet.generate_key().decode())
    ciphertext = cipher.encrypt(plaintext)

    from datetime import UTC, datetime

    rendered = repr(
        LeagueToken(
            league_id=_tokens.LEGA_MANTRA,
            ciphertext=ciphertext,
            key_fingerprint=cipher.fingerprint,
            issued_at=datetime.fromtimestamp(_tokens.IAT, tz=UTC),
            expires_at=datetime.fromtimestamp(_tokens.EXP, tz=UTC),
        )
    )

    leaked = [
        plaintext[i : i + 8]
        for i in range(len(plaintext) - 7)
        if plaintext[i : i + 8] in rendered
    ]
    assert leaked == [], f"LeagueToken.__repr__ exposes the plaintext: {leaked}"
    assert ciphertext.decode() not in rendered
    assert str(_tokens.LEGA_MANTRA) in rendered


def test_league_tokens_has_no_text_column_that_could_hold_a_jwt() -> None:
    """SPEC's bullet, in the only form that is enforceable.

    **Scoped to this one table, never metadata-wide.** Across `Base.metadata`
    the assertion is simply false — `players.nome`, `bot_state.
    last_auction_session_id` and eight columns on `player_sentiment` are all
    `Text`, legitimately. SPEC's bullet is table-scoped and this follows it.

    A real league JWT is ~800 characters, so a bounded `String` cannot hold one
    and the only unbounded column is the display name.
    """
    import fantabot.adapters.persistence.models  # noqa: F401  -- registers every table
    from fantabot.adapters.persistence.base import Base

    table = Base.metadata.tables["league_tokens"]
    text_columns = {
        c.name for c in table.columns if c.type.__class__.__name__ in {"Text", "TEXT"}
    }

    assert text_columns == {"league_name"}, (
        f"league_tokens has unbounded text columns {sorted(text_columns)}; only "
        "the display name may be unbounded"
    )

    for column in table.columns:
        if column.type.__class__.__name__ in {"String", "VARCHAR"}:
            length = getattr(column.type, "length", None)
            assert length is not None and length <= 16, (
                f"league_tokens.{column.name} is String({length}) — long enough "
                "to hold something it should not"
            )


# The instruction surface: files that tell a human what to run. `tasks/` and
# `docs/spec-*.md` are excluded deliberately — planning artefacts and archived
# specs are *records*, and amending one to remove a command that existed when it
# was written falsifies it. A repo-wide form is also unsatisfiable:
# `tasks/archive/token-store-plan.md` is tracked and names the command thirty
# times, so the guard would fail on the commit that introduced it. This file
# excludes itself for the same reason.
INSTRUCTION_SURFACE = (
    "src",
    "tests",
    "README.md",
    "CLAUDE.md",
    "data/README.md",
    "docs/lega-legamiallerotaie2.md",
    "docs/leghe-api.md",
    # This file quotes the names it is looking for, so it excludes itself. Derived
    # rather than spelled: it was `:!tests/test_token_secrecy.py`, and moving the file
    # into the mirrored tree turned the check red against its own docstring.
    f":!{SELF.relative_to(REPO)}",
)


def _grep(pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "grep", "-In", pattern, "--", *INSTRUCTION_SURFACE],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 when it finds nothing. That is the success case.
    return [line for line in out.stdout.splitlines() if line]


def test_nothing_tells_anyone_to_run_a_command_that_does_not_exist() -> None:
    """SC 21, generalised — because its original form stopped being satisfiable.

    It grepped the instruction surface for `fantabot auth`, the command deleted in
    the token-store phase. On 2026-08-30 `auth` came back as a *group* —
    `auth login`, `auth status`, `auth forget`, `auth fantalab-login` — so the
    literal now matches eight legitimate instructions, and the guard could only be
    satisfied by renaming a group the spec's rename table had settled.

    The property it protected is not about that one name: no file may tell a human
    to run something that is not there. That is now checked against the live command
    tree, which covers every command this phase renamed — a strictly larger set than
    the literal ever did.

    **Only instruction-shaped text counts.** A first draft matched any
    `fantabot <word>` and reported 54 things, almost all of them
    `from fantabot import config` and prose like "fantabot is a". A guard that cries
    wolf on an import statement gets deleted, so it looks at two shapes and no others:
    a backticked phrase, and a line that starts a shell command.
    """
    import re
    import sys

    # `tests/` has no `__init__.py`, and conftest puts only `tests/` itself on `sys.path`
    # — so a bare `from test_cli_command_set import ...` resolves only once pytest has
    # collected `tests/interface/` and added that directory too. Running *this file alone*
    # therefore failed with `ModuleNotFoundError`, which is exactly what an operator does
    # when they want to check one guard rather than the whole suite. Pre-existing; found
    # by running the review checklist instead of writing it.
    sys.path.insert(0, str(REPO / "tests" / "interface"))
    from test_cli_command_set import command_set

    known = command_set()
    groups = {c.split(" ")[0] for c in known if " " in c}
    # A group name alone is a real thing to write: "see `fantabot auth`".
    valid = known | groups

    #: `` `fantabot x y` `` or a line beginning `fantabot x y`.
    SHAPES = (
        re.compile(r"`fantabot ([a-z][a-z-]*(?: [a-z][a-z-]*)?)"),
        re.compile(r"(?:^|\s{2,}|\$ )fantabot ([a-z][a-z-]*(?: [a-z][a-z-]*)?)"),
    )

    #: A sentence saying a command *was removed* names it without instructing anyone
    #: to run it, and is the record of this phase. Degrading that prose — dropping the
    #: backticks — to satisfy a check would be the check editing the history it exists
    #: to protect. Same call as SC 27's removal notes.
    RETIRED = re.compile(r"\b(removed|deleted|retired|no longer exists|used to)\b", re.I)

    hits: list[str] = []
    for line in _grep(r"fantabot [a-z][a-z-]*"):
        _, _, text = line.partition(":")
        if RETIRED.search(text):
            continue
        for shape in SHAPES:
            for match in shape.finditer(text):
                phrase = match.group(1)
                if phrase in valid or phrase.split(" ")[0] in valid:
                    continue
                hits.append(f"{line}  ->  `fantabot {phrase}`")

    assert hits == [], (
        "these name a command that does not exist:\n  " + "\n  ".join(sorted(set(hits)))
    )


def test_nothing_still_points_at_the_deleted_auth_module() -> None:
    """The other half, and the one a command-name grep cannot see.

    `CLAUDE.md` carried two references to `auth.py` that never contained the
    string `fantabot auth`: a behavioural claim that it saves the bearer token,
    and a working rule pointing at the module by name. Both survived the
    command-name guard entirely.
    """
    # `endpoints/auth.py` is the **app's** auth route and exists. A bare `auth.py` grep
    # cannot tell the two apart, and this guard false-positived on the first citation of
    # the live one — a rule that forbids naming a file that is there is a rule people
    # route around. The path prefix is what distinguishes them.
    hits = [line for line in _grep(r"auth\.py") if "endpoints/auth.py" not in line]

    assert hits == [], (
        "these still point at the deleted module:\n  " + "\n  ".join(hits)
    )

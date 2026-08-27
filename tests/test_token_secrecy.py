"""The leak guard. SPEC calls this "the one test that matters most".

It lands before any module under ``tokens/`` exists, on purpose. A test that
matters most is not written last: the same move the previous phase made when it
landed ``test_db_boundary.py``'s import scan before a single model existed, and
the reason that boundary has held since.

Source-text and introspection, in the style of ``test_db_boundary.py:29-38`` —
not behaviour. A leak is not something you can reliably provoke at runtime; it is
something you notice a year later in a cron log. So these assertions read the
tree.

Six assertions. Three are live against today's tree and three become live as
their targets appear:

1. no JWT literal in anything git tracks                          — live
2. the key cannot be committed                                    — live
3. no key on argv                                                 — live
4. ``decrypt(`` confined to its two allowed files                 — *(deferred, proved red at T13)*
5. the AST walk over print/log/raise arguments                    — *(deferred, proved red at T13)*
6. ``cli.py``'s exclude set names the key                         — live

This file necessarily contains the patterns it forbids — a JWT-shaped literal to
test assertion 1 against, and the option strings assertion 3 rejects. Every
scan therefore excludes this file by name, and every token in it is
**synthesized**: a real one has never been in this repository and must not be.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "src" / "fantabot"
SELF = Path(__file__).resolve()

# Every JWT header segment is base64 of `{"`, so it starts `eyJ`. Requiring the
# dot after the segment keeps the pattern from matching prose about JWTs.
JWT_LITERAL = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.")

# SPEC's allowlist, verbatim. The implementation routes `auth_headers` through
# `store.load_plaintext`, so `apileague.py` is expected to stay empty of
# `decrypt(` — but it is allowed here so that a later change there fails
# deliberately, at review, rather than by surprise.
DECRYPT_ALLOWED = {
    "tokens/crypto.py",
    "tokens/store.py",
    "apileague.py",
    # Phase 5. A second service, so a second store — a lega token is a JWT whose
    # claims we read, a FantaLab session is three opaque strings. Listed here
    # rather than the assertion being widened to `tokens/*`: this test exists to
    # make each new decryption site a deliberate entry, and it did its job.
    "tokens/fantalab_store.py",
}

# argv is visible in `ps` and persists in shell history. SPEC's Never list.
FORBIDDEN_OPTIONS = ("--key", "--encryption-key", "--fernet-key", "--secret", "--token")

# Identifiers that name a credential, or a structure holding one.
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
    }
)


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


def _scanned_sources() -> list[Path]:
    """The modules that will handle a plaintext token."""
    paths = list((PACKAGE / "tokens").rglob("*.py")) if (PACKAGE / "tokens").is_dir() else []
    paths += [PACKAGE / name for name in ("apileague.py", "login.py")]
    return [p for p in paths if p.is_file()]


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


def _emits(node: ast.Call) -> bool:
    """`print(...)`, `console.print(...)`, `logger.info(...)` and friends."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if isinstance(func, ast.Attribute):
        if func.attr == "print":
            return True
        base = _base_identifier(func.value)
        return base is not None and ("log" in base.lower())
    return False


def _secret_arguments(node: ast.expr) -> list[str]:
    """Identifiers reaching an emit site, f-string interpolations included."""
    found = []
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                name = _base_identifier(value.value)
                if name in SECRET_NAMES:
                    found.append(name)
        return found
    name = _base_identifier(node)
    if name in SECRET_NAMES:
        found.append(name)
    return found


def test_no_credential_reaches_a_print_a_log_or_a_raise() -> None:
    """Assertion 5 — *(deferred, proved red at T13)*.

    An AST walk, not a regex, because `console.print(f"{lid}: {token[:8]}…")`
    must fail while `console.print(f"{n} tokens stored")` — which is SPEC's own
    example output — must pass. A substring scan cannot tell those apart.

    `Raise` is included alongside prints and logs: an exception message is a log
    line that also gets a traceback, and SPEC's `decode_claims` is explicit that
    "a truncated or wrong-format credential in a traceback is still a credential".
    """
    offenders = []
    for path in sorted(_scanned_sources()):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _emits(node):
                args: list[ast.expr] = [*node.args, *(kw.value for kw in node.keywords)]
            elif isinstance(node, ast.Raise) and node.exc is not None:
                args = node.exc.args if isinstance(node.exc, ast.Call) else [node.exc]
            else:
                continue
            for arg in args:
                for name in _secret_arguments(arg):
                    where = path.relative_to(PACKAGE)
                    offenders.append(f"{where}:{node.lineno} passes `{name}`")

    assert offenders == [], (
        f"a credential must not reach a print, a log or a traceback: {offenders}"
    )


def test_config_check_excludes_the_encryption_key() -> None:
    """Assertion 6, read out of the source rather than the output.

    An output-only assertion passes vacuously on a machine with no key set —
    which is every CI machine, and the one place this would matter least to
    catch. So the exclude-set literal itself is parsed out of `cli.py`.
    """
    tree = ast.parse((PACKAGE / "cli.py").read_text())
    excluded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "secrets" for t in node.targets
        ):
            excluded = {
                elt.value
                for elt in getattr(node.value, "elts", [])
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }

    assert "fantabot_encryption_key" in excluded, (
        f"cli.py's config-check exclude set is {sorted(excluded)} — without the "
        "key in it, `model_dump` prints the key into every cron log. "
        "`Field(repr=False)` does not suppress `model_dump`."
    )


def test_the_league_token_repr_leaks_neither_plaintext_nor_ciphertext() -> None:
    """A repr reaches tracebacks, pytest output and cron logs."""
    import sys

    sys.path.insert(0, str(REPO / "tests"))
    import _tokens
    from cryptography.fernet import Fernet

    from fantabot.db.models.tokens import LeagueToken
    from fantabot.tokens.crypto import TokenCipher

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
    import fantabot.db.models  # noqa: F401  -- registers every table
    from fantabot.db.base import Base

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
# was written falsifies it. A repo-wide form is also unsatisfiable: `tasks/plan.md`
# is tracked and names the command dozens of times, so the guard would fail on
# the commit that introduced it. This file excludes itself for the same reason.
INSTRUCTION_SURFACE = (
    "src",
    "tests",
    "README.md",
    "CLAUDE.md",
    "data/README.md",
    "docs/lega-legamiallerotaie2.md",
    "docs/leghe-api.md",
    ":!tests/test_token_secrecy.py",
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


def test_nothing_tells_anyone_to_run_the_deleted_auth_command() -> None:
    """SC 21. The command is gone; no file may still instruct someone to run it."""
    hits = _grep("fantabot auth")

    assert hits == [], (
        "these still name the deleted command:\n  " + "\n  ".join(hits)
    )


def test_nothing_still_points_at_the_deleted_auth_module() -> None:
    """The other half, and the one a command-name grep cannot see.

    `CLAUDE.md` carried two references to `auth.py` that never contained the
    string `fantabot auth`: a behavioural claim that it saves the bearer token,
    and a working rule pointing at the module by name. Both survived the
    command-name guard entirely.
    """
    hits = _grep(r"auth\.py")

    assert hits == [], (
        "these still point at the deleted module:\n  " + "\n  ".join(hits)
    )

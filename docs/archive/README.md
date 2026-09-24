# docs/archive

Closed phases. Each one leaves three files here when it closes, per the archival rule in
the root `CLAUDE.md`:

```
docs/archive/<phase>-spec.md    # what SPEC.md held
docs/archive/<phase>-plan.md    # what tasks/plan.md held
docs/archive/<phase>-todo.md    # what tasks/todo.md held
```

`SPEC.md`, `tasks/plan.md` and `tasks/todo.md` are reused by every phase, so an inbound
link to one of them silently starts describing different work. Copying them here on close,
and repointing that phase's citations at the copy in the same commit, is what stops that.

## Why this directory did not exist until 2026-09-24

The rule used to say `tasks/archive/`. `tasks/` is gitignored (`.gitignore:70`), so an
archive written there never entered git and never survived a clone — the rule named a
destination that could not hold what it was given. By the time anyone measured it, **52
references across `src/`, `tests/` and `alembic/` named documents under `tasks/archive/`
that exist on no disk and in no git history**, including four migrations citing
`tasks/w4-proofs.out` as the evidence for a schema change.

The rule's stated reason for avoiding `docs/` was that `.gitignore` ignored it too. That
stopped being true the same day: `docs/` is tracked now, except two third-party binaries
under `docs/sources/`.

**The specs closed before the change are not recoverable.** They were never committed, so
they are not in history either. Every reference to one has been repaired to name its phase
rather than a path — the provenance survives, the false path does not. If a copy turns up
on a working disk, it belongs here under the same naming, and the phase-name citations can
become paths again.

## What is already here

The four specs below closed before the rule changed and went to `docs/` directly, which is
why they survived where the `tasks/archive/` ones did not. They were renamed into this
directory on 2026-09-24 so there is one archive and not two:

| file | was |
|---|---|
| `asta-copilota-spec.md` | `docs/spec-asta-copilota.md` |
| `asta-sentiment-spec.md` | `docs/spec-asta-sentiment.md` |
| `news-sentiment-spec.md` | `docs/spec-news-sentiment.md` |
| `postgres-persistence-spec.md` | `docs/spec-postgres-persistence.md` |

A spec here is a **record**. It is not amended to rewrite history, and a claim in one that
later turned out wrong stays as written — the phase that superseded it says so in its own
documents. A *link* that no longer resolves is a different thing, and is repaired.

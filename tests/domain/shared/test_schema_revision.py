"""The database's alembic revision against the code's head, judged without a database.

A database one migration behind the code fails every write that touches a column the code
dropped, and the job log says only "lega: failed - IntegrityError". `fantabot db check`
reports a declared-but-missing *table*, so a column-only migration is invisible to it; only
a comparison of the two revisions can say so.

The comparison is pure — two sets of revision ids in, one verdict out — so every state is
pinned here with no database and no alembic. Reading the two sides is the adapters' job.
"""

from __future__ import annotations

import pytest

from fantabot.domain.shared.schema_revision import (
    SchemaState,
    ScriptRevisions,
    judge,
    unreadable,
)

HEAD = "b61ab22e8fac"
PREVIOUS = "e35dbc3294a7"
OLDER = "5839418913d5"


def _scripts(**overrides: object) -> ScriptRevisions:
    """Three revisions on one line: OLDER, then PREVIOUS, then HEAD."""
    fields: dict[str, object] = {
        "heads": (HEAD,),
        "pending": {HEAD: 0, PREVIOUS: 1, OLDER: 2},
    }
    fields.update(overrides)
    return ScriptRevisions(**fields)  # type: ignore[arg-type]


class TestAtHead:
    """The one state that needs no action."""

    def test_a_database_on_the_head_is_at_head(self) -> None:
        """The revision the scripts end in is the revision the database holds."""
        verdict = judge((HEAD,), _scripts())

        assert verdict.state is SchemaState.AT_HEAD
        assert verdict.severity == "ok"
        assert verdict.at_head is True

    def test_at_head_names_the_revision(self) -> None:
        """The line an operator reads should say which revision they are on."""
        assert HEAD in judge((HEAD,), _scripts()).summary

    def test_at_head_has_nothing_to_instruct(self) -> None:
        """No dump, no upgrade: an instruction here would be noise on a healthy screen."""
        assert judge((HEAD,), _scripts()).instruction is None

    def test_every_head_of_a_branched_history_must_be_held(self) -> None:
        """Two heads in the scripts and one in the database is not at head."""
        scripts = _scripts(heads=("aaa", "bbb"), pending={"aaa": 0, "bbb": 0, OLDER: 1})

        assert judge(("aaa", "bbb"), scripts).state is SchemaState.AT_HEAD
        assert judge(("aaa",), scripts).state is SchemaState.BEHIND

    def test_the_head_plus_a_revision_the_scripts_lack_is_not_at_head(self) -> None:
        """A database that holds the head *and* a row this checkout has never heard of was
        migrated from somewhere else; containing the head does not make it current."""
        verdict = judge((HEAD, "ffffffffffff"), _scripts())

        assert verdict.state is SchemaState.AHEAD_OR_UNKNOWN
        assert verdict.at_head is False


class TestBehind:
    """The measured failure: a database one migration short of the code."""

    def test_the_database_one_revision_short_is_behind(self) -> None:
        """The database holds the revision before the code's head."""
        verdict = judge((PREVIOUS,), _scripts())

        assert verdict.state is SchemaState.BEHIND
        assert verdict.at_head is False

    def test_behind_is_red_because_writes_already_fail(self) -> None:
        """A schema behind the code is the state in which every write to a dropped column
        fails, so it is not a yellow caution. The exit code of `db check` is not decided here."""
        assert judge((PREVIOUS,), _scripts()).severity == "error"

    def test_it_names_both_revisions_and_how_many_are_pending(self) -> None:
        """Both ids, so nothing has to be looked up, and the distance when derivable."""
        verdict = judge((OLDER,), _scripts())

        assert OLDER in verdict.summary
        assert HEAD in verdict.summary
        assert verdict.behind == 2
        assert "2 revisions" in verdict.summary

    def test_one_revision_is_not_pluralised(self) -> None:
        """The case that actually occurred reads as one revision."""
        assert "1 revision" in judge((PREVIOUS,), _scripts()).summary
        assert "1 revisions" not in judge((PREVIOUS,), _scripts()).summary

    def test_the_instruction_is_to_dump_first_and_then_upgrade(self) -> None:
        """A migration can be destructive; the operator decides, with a restore point."""
        instruction = judge((PREVIOUS,), _scripts()).instruction

        assert instruction is not None
        assert "fantabot db dump" in instruction
        assert "alembic upgrade head" in instruction
        assert instruction.index("fantabot db dump") < instruction.index("alembic upgrade head")

    def test_the_distance_is_not_claimed_when_the_database_holds_several_revisions(self) -> None:
        """Two rows in `alembic_version` is a branched database; one number would be a guess."""
        verdict = judge((PREVIOUS, OLDER), _scripts())

        assert verdict.state is SchemaState.BEHIND
        assert verdict.behind is None

    def test_a_zero_distance_is_not_printed_as_zero_revisions_to_apply(self) -> None:
        """One head of two held reads as a count of 0 for a database that is *not* at head;
        "(0 revisions to apply)" would be the wrong claim, so the count is left out."""
        scripts = _scripts(heads=("aaa", "bbb"), pending={"aaa": 0, "bbb": 0, OLDER: 1})

        verdict = judge(("aaa",), scripts)

        assert verdict.state is SchemaState.BEHIND
        assert verdict.behind is None
        assert "to apply" not in verdict.summary
        assert "0 revision" not in verdict.summary

    def test_a_branched_history_is_told_to_upgrade_heads_because_head_is_refused(self) -> None:
        """Real alembic raises "Multiple head revisions" for `upgrade head` on a branched
        history; `heads` is the argument that works there."""
        scripts = _scripts(heads=("aaa", "bbb"), pending={"aaa": 0, "bbb": 0, OLDER: 1})

        verdict = judge((OLDER,), scripts)

        assert verdict.instruction is not None
        assert "alembic upgrade heads" in verdict.instruction
        assert "upgrade head`" not in verdict.instruction
        assert "code heads aaa, bbb" in verdict.summary

    def test_a_linear_history_keeps_the_singular_head_everywhere(self) -> None:
        """The plural is for a branch only."""
        verdict = judge((PREVIOUS,), _scripts())

        assert "code head " in verdict.summary
        assert "heads" not in verdict.summary
        assert "heads" not in (verdict.instruction or "")

    def test_the_distance_is_not_claimed_when_the_adapter_could_not_derive_it(self) -> None:
        """A revision the scripts know but could not count to is still behind."""
        verdict = judge((PREVIOUS,), _scripts(pending={HEAD: 0, PREVIOUS: None}))

        assert verdict.state is SchemaState.BEHIND
        assert verdict.behind is None
        assert "revision(s)" not in verdict.summary


class TestAheadOrUnknown:
    """A revision the scripts have never heard of."""

    def test_a_database_revision_missing_from_the_scripts_is_unknown(self) -> None:
        """The database was migrated by a checkout that has migrations this one lacks."""
        verdict = judge(("ffffffffffff",), _scripts())

        assert verdict.state is SchemaState.AHEAD_OR_UNKNOWN
        assert verdict.severity == "error"

    def test_it_says_which_revision_and_does_not_prescribe_an_upgrade(self) -> None:
        """`alembic upgrade head` cannot fix this and might fail in a confusing way."""
        verdict = judge(("ffffffffffff",), _scripts())

        assert "ffffffffffff" in verdict.summary
        assert verdict.instruction is not None
        assert "upgrade head" not in verdict.instruction

    def test_one_unknown_row_among_known_ones_is_still_unknown(self) -> None:
        """Unknown outranks behind: the known row would otherwise hide it."""
        assert judge((PREVIOUS, "ffffffffffff"), _scripts()).state is SchemaState.AHEAD_OR_UNKNOWN


class TestUnversioned:
    """No usable `alembic_version`: nothing says which migrations the database has."""

    def test_no_table_is_unversioned(self) -> None:
        """`None` is what the adapter returns when `alembic_version` does not exist."""
        verdict = judge(None, _scripts())

        assert verdict.state is SchemaState.UNVERSIONED
        assert verdict.severity == "error"

    def test_a_table_with_no_row_is_unversioned(self) -> None:
        """`alembic stamp base`, or a table created and never written."""
        assert judge((), _scripts()).state is SchemaState.UNVERSIONED

    def test_the_two_causes_read_differently(self) -> None:
        """They have different histories, so the screen must not merge them."""
        assert judge(None, _scripts()).summary != judge((), _scripts()).summary

    def test_no_table_is_worded_as_a_missing_table(self) -> None:
        """Not the other cause's sentence: the table is not there to hold anything."""
        verdict = judge(None, _scripts())

        assert verdict.table_missing is True
        assert "no alembic_version table" in verdict.summary

    def test_a_table_with_no_row_is_worded_as_an_empty_table(self) -> None:
        """The table exists; it is its contents that are missing."""
        verdict = judge((), _scripts())

        assert verdict.table_missing is False
        assert "holds no revision" in verdict.summary

    def test_it_is_unversioned_even_when_the_scripts_cannot_be_found(self) -> None:
        """A database fact is true whatever the install can see."""
        assert judge(None, None).state is SchemaState.UNVERSIONED

    def test_it_does_not_recommend_upgrading_blind(self) -> None:
        """`upgrade head` on an unversioned database tries to create tables that exist."""
        instruction = judge(None, _scripts()).instruction

        assert instruction is not None
        assert "fantabot db dump" in instruction
        assert "upgrade head" not in instruction


class TestEmpty:
    """A database with none of the application's tables: nothing to dump, nothing to lose."""

    def test_no_revision_and_no_tables_is_empty_not_unversioned(self) -> None:
        """A fresh `fantabot-app db create`, which has no `alembic_version` either."""
        verdict = judge(None, _scripts(), empty=True)

        assert verdict.state is SchemaState.EMPTY
        assert verdict.at_head is False
        assert verdict.severity == "warn"

    def test_a_database_taken_back_to_base_is_empty_too(self) -> None:
        """`alembic downgrade base` leaves the version table and no row."""
        assert judge((), _scripts(), empty=True).state is SchemaState.EMPTY

    def test_it_is_told_to_upgrade_with_no_dump_first(self) -> None:
        """The advice an empty database gets must agree with the line `db check` prints
        under it, and a dump of nothing is noise."""
        instruction = judge(None, _scripts(), empty=True).instruction

        assert instruction is not None
        assert "alembic upgrade head" in instruction
        assert "dump" not in instruction

    def test_a_branched_history_is_created_with_heads(self) -> None:
        """`head` is refused when there are two."""
        scripts = _scripts(heads=("aaa", "bbb"), pending={"aaa": 0, "bbb": 0})

        instruction = judge(None, scripts, empty=True).instruction

        assert instruction is not None
        assert "alembic upgrade heads" in instruction

    def test_without_scripts_the_instruction_still_names_a_command(self) -> None:
        """An install that cannot read the migrations still knows what creates the schema."""
        instruction = judge(None, None, empty=True).instruction

        assert instruction is not None
        assert "alembic upgrade head" in instruction

    def test_the_summary_says_there_are_no_tables(self) -> None:
        """What an operator needs to see to believe the advice."""
        assert "no tables" in judge(None, _scripts(), empty=True).summary

    def test_a_revision_outranks_emptiness(self) -> None:
        """`empty` is read only when there is no revision: a database that holds one is
        judged on it, whatever the caller thinks of its tables."""
        assert judge((HEAD,), _scripts(), empty=True).state is SchemaState.AT_HEAD
        assert judge((PREVIOUS,), _scripts(), empty=True).state is SchemaState.BEHIND

    def test_no_revision_with_tables_present_stays_unversioned(self) -> None:
        """The default: a populated database with no version table is the dangerous one."""
        assert judge(None, _scripts(), empty=False).state is SchemaState.UNVERSIONED


class TestScriptsUnavailable:
    """An installed wheel has no `alembic.ini` beside it to compare against."""

    def test_no_scripts_is_not_a_mismatch(self) -> None:
        """The database may be perfectly current; this install just cannot tell."""
        verdict = judge((HEAD,), None)

        assert verdict.state is SchemaState.SCRIPTS_UNAVAILABLE
        assert verdict.severity == "warn"
        assert verdict.at_head is False

    def test_it_still_reports_what_the_database_holds(self) -> None:
        """Half the comparison is still information."""
        assert HEAD in judge((HEAD,), None).summary


class TestUnreadable:
    """The database could not be asked."""

    def test_an_unreadable_database_says_it_could_not_be_checked(self) -> None:
        """A failure to determine the revision is a verdict of its own, never an exception."""
        verdict = unreadable("OperationalError")

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.severity == "warn"
        assert "could not be checked" in verdict.summary

    def test_only_the_exception_type_is_carried(self) -> None:
        """A driver's message holds the DSN; the type name is all a log line gets."""
        assert "OperationalError" in unreadable("OperationalError").summary
        assert unreadable("OperationalError").error == "OperationalError"


class TestEveryStateIsAccountedFor:
    """The three surfaces key off `severity` and `at_head`; neither may go missing."""

    @pytest.mark.parametrize(
        "verdict",
        [
            judge((HEAD,), _scripts()),
            judge((PREVIOUS,), _scripts()),
            judge(("ffffffffffff",), _scripts()),
            judge(None, _scripts()),
            judge(None, _scripts(), empty=True),
            judge((HEAD,), None),
            unreadable("OperationalError"),
        ],
    )
    def test_each_verdict_has_a_summary_and_a_known_severity(self, verdict: object) -> None:
        """No state falls through to an empty line or an unstyled one."""
        assert verdict.summary  # type: ignore[attr-defined]
        assert verdict.severity in {"ok", "warn", "error"}  # type: ignore[attr-defined]

    def test_only_at_head_is_ok(self) -> None:
        """Green is reserved for the one state that needs nothing from the operator."""
        severities = {
            state: v.severity
            for state, v in {
                SchemaState.AT_HEAD: judge((HEAD,), _scripts()),
                SchemaState.BEHIND: judge((PREVIOUS,), _scripts()),
                SchemaState.EMPTY: judge(None, _scripts(), empty=True),
                SchemaState.AHEAD_OR_UNKNOWN: judge(("ffffffffffff",), _scripts()),
                SchemaState.UNVERSIONED: judge(None, _scripts()),
                SchemaState.SCRIPTS_UNAVAILABLE: judge((HEAD,), None),
                SchemaState.UNREADABLE: unreadable("OperationalError"),
            }.items()
        }

        assert set(severities) == set(SchemaState), "a state was added without being judged here"
        assert [s for s, sev in severities.items() if sev == "ok"] == [SchemaState.AT_HEAD]

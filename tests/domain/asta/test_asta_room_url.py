"""A pasted link becomes a fantaleague id, or says plainly what to paste instead.

Three shapes reach an operator, and they are not interchangeable.

`app.fantalab.it/asta?asta=<uuid>` is the room. The `asta` query parameter **is** the
fantaleague id, not a separate handle (`docs/fantalab/03-platform-map.md`), so this resolves
with no network at all.

`app.fantalab.it/join-asta?invitation_id=<uuid>` is what an admin actually sends, and its
uuid is a *different* id — an invitation, not a league (`docs/fantalab/06 §3`). It cannot be
turned into a fantaleague id without `POST /fantaleague/fetchByInvitation` and a Bearer, so
this layer names it rather than guessing. An earlier draft of the plan refused it as
"unsupported", which would have handed the operator a dead end holding the one link they had.

A bare uuid is accepted because `harvest scan` prints them and pasting one back is natural.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.live import InvitationLink, parse_room_url

FL = "00cee3f1-1a1b-4c8d-9e2f-7a6b5c4d3e2f"
INVITE = "11aa22bb-3c4d-5e6f-7a8b-9c0d1e2f3a4b"


class TestARoomLink:
    def test_the_asta_query_parameter_is_the_fantaleague_id(self) -> None:
        assert parse_room_url(f"https://app.fantalab.it/asta?asta={FL}") == FL

    def test_other_query_parameters_do_not_confuse_it(self) -> None:
        assert parse_room_url(f"https://app.fantalab.it/asta?ref=x&asta={FL}&t=1") == FL

    def test_a_trailing_slash_or_fragment_is_tolerated(self) -> None:
        assert parse_room_url(f"https://app.fantalab.it/asta/?asta={FL}#room") == FL

    def test_surrounding_whitespace_is_stripped(self) -> None:
        """Pasting from a chat client brings a newline more often than not."""
        assert parse_room_url(f"  https://app.fantalab.it/asta?asta={FL}\n") == FL


class TestABareUuid:
    def test_it_is_accepted_as_itself(self) -> None:
        assert parse_room_url(FL) == FL

    def test_a_bare_word_that_is_not_a_uuid_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a FantaLab room"):
            parse_room_url("legamiallerotaie2")


class TestAnInviteLink:
    def test_it_raises_something_the_caller_can_act_on(self) -> None:
        """Not a `ValueError` among others: the caller has to be able to tell "wrong link"
        from "right link, one authenticated call away" and say so differently."""
        with pytest.raises(InvitationLink) as caught:
            parse_room_url(f"https://app.fantalab.it/join-asta?invitation_id={INVITE}")

        assert caught.value.invitation_id == INVITE

    def test_the_message_names_the_endpoint_that_would_resolve_it(self) -> None:
        with pytest.raises(InvitationLink, match="fetchByInvitation"):
            parse_room_url(f"https://app.fantalab.it/join-asta?invitation_id={INVITE}")

    def test_an_invitation_id_is_not_silently_used_as_a_league_id(self) -> None:
        """They are different uuids. Passing one where the other is meant would subscribe to
        a room that does not exist and wait for a lot that never comes."""
        with pytest.raises(InvitationLink):
            parse_room_url(f"https://app.fantalab.it/join-asta?invitation_id={FL}")


class TestNothingUsable:
    @pytest.mark.parametrize("text", ["", "   ", "https://app.fantalab.it/asta", "not a url"])
    def test_it_refuses_rather_than_returning_an_empty_id(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_room_url(text)

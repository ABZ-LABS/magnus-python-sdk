import uuid

import pytest

from iamagnus import StreamError


@pytest.mark.unit
class TestConversation:
    def test_the_first_turn_opens_a_session(self, client, magnus):
        chat = client.conversation("magnus_standard")
        assert chat.session_id is None
        chat.send("Hola")
        assert chat.session_id
        assert chat.session_source == "new"

    def test_later_turns_carry_the_session_forward(self, client, magnus):
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        first = chat.session_id
        chat.send("¿Y el precio?")
        assert magnus.requests[-1]["body"]["session_id"] == first

    def test_the_session_the_server_ran_on_wins_over_the_one_sent(self, client, magnus):
        """The pipeline rolls the session on persona/user/org change mid-turn.

        Echoing the request value back would pin the thread to a conversation
        the server has already abandoned.
        """
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        rolled = str(uuid.uuid4())
        magnus.rolled_session_id = rolled
        chat.send("Otra cosa")
        assert chat.session_id == rolled

    def test_correlation_ids_are_exposed_for_joining_against_traces(self, client, magnus):
        magnus.trace_id, magnus.turn_id = "trace-42", "turn-42"
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        assert chat.last_trace_id == "trace-42"
        assert chat.last_turn_id == "turn-42"

    def test_usage_and_its_provenance_are_kept_per_turn(self, client, magnus):
        magnus.usage_source = "estimated"
        magnus.usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        assert chat.last_usage["total_tokens"] == 3
        assert chat.last_usage_source == "estimated"

    def test_reset_starts_a_new_conversation(self, client, magnus):
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        chat.reset()
        chat.send("Empecemos de nuevo")
        assert "session_id" not in magnus.requests[-1]["body"]

    def test_the_client_user_is_inherited(self, client, magnus):
        client.user = "juan@empresa.com"
        client.conversation("magnus_standard").send("Hola")
        assert magnus.requests[-1]["body"]["user"] == "juan@empresa.com"

    def test_a_conversation_can_resume_a_known_session(self, client, magnus):
        sid = str(uuid.uuid4())
        chat = client.conversation("magnus_standard", session_id=sid)
        chat.send("Seguimos")
        assert magnus.requests[-1]["body"]["session_id"] == sid

    def test_resuming_with_a_non_uuid_fails_immediately(self, client):
        with pytest.raises(ValueError, match="must be a UUID"):
            client.conversation("magnus_standard", session_id="nope")


@pytest.mark.unit
class TestConversationStreaming:
    def test_a_streamed_turn_advances_the_session(self, client, magnus):
        chat = client.conversation("magnus_standard")
        stream = chat.stream("Hola")
        list(stream)
        assert chat.session_id == stream.session_id
        assert chat.session_id

    def test_a_streamed_turn_that_failed_still_advanced_the_conversation(
        self, client, magnus,
    ):
        """It ran. Dropping the session would silently fork the thread."""
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        known = chat.session_id
        magnus.stream_mode = "error"
        with pytest.raises(StreamError):
            list(chat.stream("Y esto"))
        assert chat.session_id == known


@pytest.mark.unit
class TestHandoff:
    """A person from the team can take a conversation over from the agent.

    The server keeps answering 200 — the turn where the agent hands off, then a
    fixed notice — so without the flag a caller cannot tell a person is in charge.
    """

    def test_a_conversation_starts_with_the_agent(self, client, magnus):
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        assert chat.handoff is False

    def test_the_flag_follows_the_server_turn_by_turn(self, client, magnus):
        chat = client.conversation("magnus_standard")
        magnus.handoff = True
        chat.send("Quiero hablar con una persona")
        assert chat.handoff is True
        magnus.handoff = False
        chat.send("Hola de nuevo")
        assert chat.handoff is False

    def test_a_streamed_turn_reports_it_too(self, client, magnus):
        magnus.handoff = True
        chat = client.conversation("magnus_standard")
        stream = chat.stream("Hola")
        "".join(stream)
        assert chat.handoff is True

    def test_a_server_without_the_field_is_not_a_handoff(self, client, magnus):
        magnus.handoff = None
        chat = client.conversation("magnus_standard")
        chat.send("Hola")
        assert chat.handoff is False

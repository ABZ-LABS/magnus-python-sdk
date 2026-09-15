import pytest

from iamagnus import StreamError


@pytest.mark.unit
class TestStreaming:
    def test_deltas_arrive_one_by_one(self, client, magnus):
        magnus.reply = "Hola que tal"
        magnus.stream_mode = "tokens"
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        assert list(stream) == ["Hola", " que", " tal"]

    def test_the_whole_text_is_available_when_the_stream_ends(self, client, magnus):
        magnus.reply = "Hola que tal"
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        list(stream)
        assert stream.text == "Hola que tal"
        assert stream.finish_reason == "stop"

    def test_a_turn_delivered_whole_still_arrives(self, client, magnus):
        """The server may deliver a turn whole; it then comes as one delta, not zero."""
        magnus.stream_mode = "single"
        magnus.reply = "Respuesta entera"
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        assert list(stream) == ["Respuesta entera"]
        assert stream.text == "Respuesta entera"

    def test_extensions_are_merged_from_whichever_chunk_carries_them(self, client, magnus):
        """Live path puts them on the closing chunk, buffered path on the first."""
        for mode in ("tokens", "single"):
            magnus.stream_mode = mode
            stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
            list(stream)
            assert stream.session_id, f"no session id in {mode} mode"
            assert stream.magnus["session_source"] in ("new", "explicit")
            assert stream.magnus["usage_source"] == "measured"

    def test_usage_is_withheld_unless_asked_for(self, client):
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        list(stream)
        assert stream.usage is None

    def test_include_usage_yields_a_final_usage_chunk(self, client, magnus):
        magnus.usage = {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
        stream = client.stream_chat(
            "magnus_standard", [{"role": "user", "content": "hi"}], include_usage=True,
        )
        list(stream)
        assert stream.usage == magnus.usage

    def test_include_usage_is_requested_in_the_documented_shape(self, client, magnus):
        client.stream_chat(
            "magnus_standard", [{"role": "user", "content": "hi"}], include_usage=True,
        )
        body = magnus.requests[-1]["body"]
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}


@pytest.mark.unit
class TestAFailureThatLooksLikeSuccess:
    """The status line went out as 200 before the turn failed.

    A client that reads only `delta.content` hands back a truncated answer as
    though it were the real one. This is the whole reason the stream is parsed
    rather than concatenated.
    """

    def test_a_mid_stream_error_raises(self, client, magnus):
        magnus.stream_mode = "error"
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        with pytest.raises(StreamError):
            list(stream)

    def test_the_raised_error_carries_the_server_envelope(self, client, magnus):
        magnus.stream_mode = "error"
        magnus.stream_error = {
            "message": "El agente no pudo completar el turno.",
            "type": "server_error", "param": None, "code": "pipeline_failed",
        }
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        with pytest.raises(StreamError) as exc:
            list(stream)
        assert exc.value.code == "pipeline_failed"
        assert exc.value.type == "server_error"
        assert "no pudo completar" in exc.value.message

    def test_what_the_reader_already_saw_is_kept(self, client, magnus):
        """Contradicting bytes already delivered is worse than truncating."""
        magnus.stream_mode = "error"
        magnus.reply = "Hola mundo"
        stream = client.stream_chat("magnus_standard", [{"role": "user", "content": "hi"}])
        seen = []
        with pytest.raises(StreamError) as exc:
            for delta in stream:
                seen.append(delta)
        assert "".join(seen) == exc.value.partial_text
        assert exc.value.partial_text == "Hola "

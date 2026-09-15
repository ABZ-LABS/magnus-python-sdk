import uuid

import pytest

from iamagnus import InvalidRequestError, UnsupportedParameterError


@pytest.mark.unit
class TestChat:
    def test_returns_the_full_openai_shaped_response(self, client, magnus):
        magnus.reply = "Buenas."
        res = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert res["choices"][0]["message"]["content"] == "Buenas."
        assert res["object"] == "chat.completion"

    def test_send_message_returns_only_the_text(self, client, magnus):
        magnus.reply = "Listo."
        assert client.send_message("magnus_standard", "Hola") == "Listo."

    def test_the_user_field_is_sent_for_multi_tenant_attribution(self, client, magnus):
        client.user = "juan@empresa.com"
        client.send_message("magnus_standard", "Hola")
        assert magnus.requests[-1]["body"]["user"] == "juan@empresa.com"

    def test_a_per_call_user_overrides_the_client_default(self, client, magnus):
        client.user = "default@x.com"
        client.send_message("magnus_standard", "Hola", user="otro@x.com")
        assert magnus.requests[-1]["body"]["user"] == "otro@x.com"

    def test_multimodal_content_is_passed_through(self, client, magnus):
        """The server flattens the text parts; the client must not mangle them first."""
        content = [
            {"type": "text", "text": "¿Qué es esto?"},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
        ]
        client.send_message("magnus_standard", content)
        assert magnus.requests[-1]["body"]["messages"][-1]["content"] == content

    def test_the_usage_block_reaches_the_caller(self, client, magnus):
        magnus.usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
        res = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert res["usage"]["total_tokens"] == 7

    def test_usage_source_says_whether_the_tokens_are_real(self, client, magnus):
        """`estimated` is a character heuristic — nobody should bill on it unknowingly."""
        magnus.usage_source = "estimated"
        res = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert res["magnus"]["usage_source"] == "estimated"


@pytest.mark.unit
class TestRefusedParameters:
    """Magnus refuses these rather than ignoring them; the SDK must say which."""

    @pytest.mark.parametrize("param,value", [
        ("tools", [{"type": "function", "function": {"name": "f"}}]),
        ("tool_choice", "auto"),
        ("functions", [{"name": "f"}]),
        ("function_call", "auto"),
        ("response_format", {"type": "json_object"}),
    ])
    def test_an_unsupported_parameter_names_itself(self, client, magnus, param, value):
        magnus.force(400, {"error": {
            "message": f"'{param}' is not supported by this endpoint.",
            "type": "invalid_request_error", "param": param,
            "code": "unsupported_parameter",
        }})
        with pytest.raises(UnsupportedParameterError) as exc:
            client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert exc.value.param == param
        assert exc.value.code == "unsupported_parameter"
        assert isinstance(exc.value, InvalidRequestError)

    def test_an_image_only_turn_is_a_typed_400(self, client):
        with pytest.raises(InvalidRequestError) as exc:
            client.chat("magnus_standard", [{
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "x"}}],
            }])
        assert exc.value.status == 400
        assert exc.value.param == "messages"


@pytest.mark.unit
class TestSession:
    def test_a_session_id_is_sent_when_given(self, client, magnus):
        sid = str(uuid.uuid4())
        client.chat("magnus_standard", [{"role": "user", "content": "Hola"}], session_id=sid)
        assert magnus.requests[-1]["body"]["session_id"] == sid

    def test_a_non_uuid_session_id_fails_before_the_round_trip(self, client, magnus):
        """The server 400s on this; failing locally names the caller's own bug."""
        with pytest.raises(ValueError, match="must be a UUID"):
            client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                        session_id="not-a-uuid")
        assert magnus.requests == []

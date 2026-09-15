"""The error envelope is the SDK's most-used surface after `.send()`.

Losing it — which is what `raise_for_status()` did — turns "you sent a
parameter Magnus refuses" and "your key expired" into the same string.
"""
import pytest

from iamagnus import (
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    MagnusAPIError,
    MagnusClient,
    NotFoundError,
    RateLimitError,
    ServerError,
    UnsupportedParameterError,
)
from iamagnus.errors import error_from_response


def _envelope(message, type_="invalid_request_error", param=None, code=None):
    return {"error": {"message": message, "type": type_, "param": param, "code": code}}


@pytest.mark.unit
class TestErrorMapping:
    @pytest.mark.parametrize("status,expected", [
        (400, InvalidRequestError),
        (401, AuthenticationError),
        (404, NotFoundError),
        (409, ConflictError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (418, MagnusAPIError),
    ])
    def test_each_status_gets_its_own_type(self, status, expected):
        error = error_from_response(status, _envelope("x"), {})
        assert type(error) is expected
        assert error.status == status

    def test_unsupported_parameter_beats_the_status_mapping(self):
        error = error_from_response(
            400, _envelope("no", param="tools", code="unsupported_parameter"), {},
        )
        assert isinstance(error, UnsupportedParameterError)
        assert error.param == "tools"

    def test_every_envelope_field_survives(self):
        error = error_from_response(
            400, _envelope("bad", type_="invalid_request_error", param="n", code="c"), {},
        )
        assert (error.message, error.type, error.param, error.code) == (
            "bad", "invalid_request_error", "n", "c",
        )

    def test_retry_after_is_read_as_an_int(self):
        error = error_from_response(429, _envelope("slow"), {"Retry-After": "12"})
        assert error.retry_after == 12

    def test_a_missing_retry_after_is_none_not_a_crash(self):
        assert error_from_response(429, _envelope("slow"), {}).retry_after is None

    def test_a_garbage_retry_after_is_none_not_a_crash(self):
        error = error_from_response(429, _envelope("slow"), {"Retry-After": "Wed, 21 Oct"})
        assert error.retry_after is None

    def test_a_non_envelope_body_keeps_a_readable_slice(self):
        """A proxy error page, or HTML from the wrong host. '502' alone locates nothing."""
        error = error_from_response(502, "<html>nginx bad gateway</html>", {})
        assert error.status == 502
        assert "nginx" in error.message

    def test_an_empty_body_still_produces_a_message(self):
        assert "504" in error_from_response(504, None, {}).message

    def test_the_string_form_leads_with_what_to_act_on(self):
        error = error_from_response(
            400, _envelope("no tools", param="tools", code="unsupported_parameter"), {},
        )
        text = str(error)
        assert "400" in text and "unsupported_parameter" in text and "tools" in text


@pytest.mark.unit
class TestErrorsOverTheWire:
    def test_an_unknown_path_is_a_typed_not_found(self, client):
        with pytest.raises(NotFoundError):
            client._request("GET", "/v1/nope")

    def test_a_dead_host_is_a_connection_error_not_a_stack_trace(self):
        from iamagnus import MagnusConnectionError

        # Port 1 on loopback: nothing listens, and it fails fast.
        with MagnusClient("http://127.0.0.1:1", "k", max_retries=0, timeout=2) as client:
            with pytest.raises(MagnusConnectionError) as exc:
                client.health()
        assert "127.0.0.1:1" in str(exc.value)

    def test_the_response_headers_are_kept_on_the_error(self, magnus):
        magnus.force(429, _envelope("slow", type_="rate_limit_error",
                                    code="rate_limit_exceeded"),
                     {"Retry-After": "3", "X-RateLimit-Reset": "later"})
        with MagnusClient(magnus.url, "k", max_retries=0) as client:
            with pytest.raises(RateLimitError) as exc:
                client.list_agents()
        assert exc.value.retry_after == 3
        assert exc.value.headers["X-RateLimit-Reset"] == "later"

import pytest

from iamagnus import ConflictError, MagnusClient, RateLimitError, ServerError

RATE_LIMIT_BODY = {"error": {
    "message": "Rate limit reached for this API key.",
    "type": "rate_limit_error", "param": None, "code": "rate_limit_exceeded",
}}


@pytest.mark.unit
class TestIdempotency:
    def test_the_key_is_sent_as_a_header(self, client, magnus):
        client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                    idempotency_key="key-1")
        assert magnus.requests[-1]["headers"]["idempotency-key"] == "key-1"

    def test_a_repeat_replays_instead_of_running_a_second_turn(self, client, magnus):
        """A turn advances the conversation and can run tools; running it twice is a bug."""
        first = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                            idempotency_key="key-1")
        second = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                             idempotency_key="key-1")
        assert first == second
        assert magnus.turns_run == 1

    def test_a_turn_still_in_flight_is_a_conflict_not_a_retry(self, client, magnus):
        magnus.idempotency["key-1"] = "IN_FLIGHT"
        with pytest.raises(ConflictError) as exc:
            client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                        idempotency_key="key-1")
        assert exc.value.status == 409
        assert exc.value.code == "request_in_progress"

    def test_no_key_means_nothing_is_reserved(self, client, magnus):
        client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert "idempotency-key" not in magnus.requests[-1]["headers"]


@pytest.mark.unit
class TestRetries:
    def test_a_get_retries_through_a_429(self, magnus):
        magnus.force(429, RATE_LIMIT_BODY, {"Retry-After": "0"})
        with MagnusClient(magnus.url, "k", max_retries=2) as client:
            assert client.list_agents()  # the retry succeeded

    def test_a_get_gives_up_with_the_typed_error(self, magnus):
        for _ in range(3):
            magnus.force(429, RATE_LIMIT_BODY, {"Retry-After": "0"})
        with MagnusClient(magnus.url, "k", max_retries=2) as client:
            with pytest.raises(RateLimitError) as exc:
                client.list_agents()
        assert exc.value.status == 429
        assert exc.value.code == "rate_limit_exceeded"

    def test_retry_after_is_read_off_the_header(self, magnus):
        magnus.force(429, RATE_LIMIT_BODY, {"Retry-After": "7"})
        with MagnusClient(magnus.url, "k", max_retries=0) as client:
            with pytest.raises(RateLimitError) as exc:
                client.list_agents()
        assert exc.value.retry_after == 7

    def test_a_bare_post_is_never_retried(self, magnus):
        """Without an Idempotency-Key a retry runs the pipeline a second time."""
        magnus.force(503, {"error": {"message": "upstream down",
                                     "type": "server_error", "param": None, "code": None}})
        with MagnusClient(magnus.url, "k", max_retries=3) as client:
            with pytest.raises(ServerError):
                client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        posts = [r for r in magnus.requests if r["method"] == "POST"]
        assert len(posts) == 1

    def test_a_post_with_an_idempotency_key_is_retried(self, magnus):
        """Safe: the server replays rather than re-running."""
        magnus.force(503, {"error": {"message": "upstream down",
                                     "type": "server_error", "param": None, "code": None}})
        with MagnusClient(magnus.url, "k", max_retries=2) as client:
            res = client.chat("magnus_standard", [{"role": "user", "content": "Hola"}],
                              idempotency_key="key-1")
        assert res["choices"][0]["message"]["content"]
        assert len([r for r in magnus.requests if r["method"] == "POST"]) == 2

    def test_a_4xx_is_not_retried(self, magnus):
        magnus.force(400, {"error": {"message": "bad", "type": "invalid_request_error",
                                     "param": "messages", "code": None}})
        with MagnusClient(magnus.url, "k", max_retries=3) as client:
            with pytest.raises(Exception):
                client.list_agents()
        assert len([r for r in magnus.requests if r["path"] == "/v1/models"]) == 1

    def test_a_stream_is_never_retried(self, magnus):
        """It has no idempotency key, so a retry silently runs the turn again."""
        magnus.force(503, {"error": {"message": "down", "type": "server_error",
                                     "param": None, "code": None}})
        with MagnusClient(magnus.url, "k", max_retries=3) as client:
            with pytest.raises(ServerError):
                client.stream_chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert len([r for r in magnus.requests if r["method"] == "POST"]) == 1


@pytest.mark.unit
class TestRateLimitBudget:
    def test_the_remaining_budget_is_tracked_off_every_response(self, client, magnus):
        magnus.rate_limit_remaining = 42
        client.chat("magnus_standard", [{"role": "user", "content": "Hola"}])
        assert client.rate_limit_remaining == 42
        assert client.rate_limit_reset == magnus.rate_limit_reset

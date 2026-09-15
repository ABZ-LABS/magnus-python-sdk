import pytest

from iamagnus import AuthenticationError, MagnusClient
from tests.mock_magnus import MockMagnus


@pytest.mark.unit
class TestModels:
    def test_lists_the_agents_the_key_can_reach(self, client, magnus):
        agents = client.list_agents()
        assert [a["id"] for a in agents] == magnus.agents
        assert agents[0]["object"] == "model"
        assert agents[0]["owned_by"] == "magnus"

    def test_retrieves_one_agent(self, client):
        agent = client.get_agent("porteria")
        assert agent["id"] == "porteria"

    def test_magnus_is_an_alias_for_magnus_standard(self, client):
        assert client.get_agent("magnus")["id"] == "magnus_standard"

    def test_an_unknown_agent_is_none_not_an_exception(self, client):
        """404 here means 'no such persona', which is an answer, not a failure."""
        assert client.get_agent("no_such_agent") is None

    def test_an_agent_id_with_a_slash_is_escaped(self, client, magnus):
        client.get_agent("weird/id")
        assert magnus.requests[-1]["path"] == "/v1/models/weird%2Fid"


@pytest.mark.unit
class TestAuth:
    def test_a_missing_key_is_an_authentication_error(self, magnus):
        with MagnusClient(magnus.url, "", max_retries=0) as client:
            with pytest.raises(AuthenticationError) as exc:
                client.list_agents()
        assert exc.value.status == 401

    def test_the_bearer_header_carries_the_key(self, client, magnus):
        client.list_agents()
        assert magnus.requests[-1]["headers"]["authorization"] == "Bearer magnus_test_key"

    def test_x_api_key_is_accepted_too(self, magnus):
        """Documented as supported by the server; a proxy may strip one or the other."""
        with MagnusClient(magnus.url, "k", auth_scheme="x-api-key", max_retries=0) as client:
            client.list_agents()
        headers = magnus.requests[-1]["headers"]
        assert headers["x-api-key"] == "k"
        assert "authorization" not in headers

    def test_an_unknown_auth_scheme_is_refused_at_construction(self, magnus):
        with pytest.raises(ValueError):
            MagnusClient(magnus.url, "k", auth_scheme="basic")


@pytest.mark.unit
class TestHealth:
    def test_health_needs_no_key(self, magnus):
        with MagnusClient(magnus.url, "", max_retries=0) as client:
            assert client.health()["status"] == "ok"

    def test_health_is_outside_the_v1_prefix(self, client, magnus):
        client.health()
        assert magnus.requests[-1]["path"] == "/api/health/simple"


@pytest.mark.unit
class TestBaseURL:
    """The base URL is configuration, not a constant.

    The SDK is pointed at production, staging or a local server by its caller;
    nothing about iamagnus.com is baked into the client.
    """

    def test_requests_go_to_the_configured_host(self, magnus):
        with MagnusClient(magnus.url, "k", max_retries=0) as client:
            assert client.base_url == magnus.url
            assert client.list_agents()
        assert magnus.requests

    def test_a_trailing_slash_is_trimmed(self, magnus):
        # The most common way to end up requesting //v1/models.
        with MagnusClient(magnus.url + "/", "k", max_retries=0) as client:
            client.list_agents()
        assert magnus.requests[-1]["path"] == "/v1/models"

    def test_an_empty_base_url_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="base_url is required"):
            MagnusClient("", "k")

    def test_two_clients_can_point_at_different_deployments(self, magnus):
        """Staging and production side by side is a normal thing to want."""
        other = MockMagnus()
        other.agents = ["solo_en_el_otro"]
        try:
            with MagnusClient(magnus.url, "k", max_retries=0) as a, \
                 MagnusClient(other.url, "k", max_retries=0) as b:
                assert [x["id"] for x in b.list_agents()] == ["solo_en_el_otro"]
                assert [x["id"] for x in a.list_agents()] == magnus.agents
        finally:
            other.close()

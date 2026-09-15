import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iamagnus import MagnusClient  # noqa: E402
from tests.mock_magnus import MockMagnus  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ambient_magnus_env(monkeypatch):
    """A developer's own MAGNUS_BASE_URL must not leak into the unit suite.

    The livecheck reads these as defaults; inherited, they would point the gate
    at a real deployment from inside a unit test.
    """
    for name in ("MAGNUS_BASE_URL", "MAGNUS_API_KEY", "MAGNUS_AGENT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def magnus():
    server = MockMagnus()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def client(magnus):
    # No retries by default: a test that wants them asks for them, and the rest
    # fail fast instead of sleeping through a backoff.
    with MagnusClient(magnus.url, "magnus_test_key", max_retries=0, timeout=10) as c:
        yield c

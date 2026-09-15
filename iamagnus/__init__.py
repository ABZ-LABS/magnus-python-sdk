"""Magnus Python SDK — talk to Magnus agents over the `/v1` API.

    from iamagnus import MagnusClient

    with MagnusClient("https://api.iamagnus.com", "magnus_sys_...") as client:
        agent = client.list_agents()[0]["id"]

        # A thread. The session id — not resent history — is what continues it.
        chat = client.conversation(agent, user="jane@company.com")
        print(chat.send("Hi, what can you do?"))
        print(chat.send("And the price?"))

        # Streamed
        for delta in chat.stream("Tell me more"):
            print(delta, end="", flush=True)

Authentication is a System API Key or User API Key from the Magnus dashboard, sent
as `Authorization: Bearer <key>` (or `X-API-Key` via `auth_scheme`).
"""
__version__ = "0.1.0"

from .client import Conversation, MagnusClient
from ._stream import ChatStream
from .errors import (
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    MagnusAPIError,
    MagnusConnectionError,
    MagnusError,
    MagnusTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StreamError,
    UnsupportedParameterError,
)

__all__ = [
    "__version__",
    "MagnusClient",
    "Conversation",
    "ChatStream",
    "MagnusError",
    "MagnusAPIError",
    "MagnusConnectionError",
    "MagnusTimeoutError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "InvalidRequestError",
    "UnsupportedParameterError",
    "ConflictError",
    "RateLimitError",
    "ServerError",
    "StreamError",
]

"""HTTP client for the Magnus `/v1` API (OpenAI-compatible surface).

Magnus exposes its agents as OpenAI "models". The envelope is OpenAI's; the
semantics are not, and the differences are what this client exists to hide:

- **history is not state.** The server reads only the last user message and
  keeps conversation state server-side, so resending history does not restore a
  thread. `session_id` does. Prefer :meth:`MagnusClient.conversation`.
- **the agent owns the turn.** `tools`, `response_format` and friends are
  refused rather than ignored, because a silently dropped `response_format` is
  worse than a 400.
- **a streamed turn can fail after HTTP 200.** See :mod:`iamagnus._stream`.

See CONTRACT.md for the wire format this client is written against.
"""
import random
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

import requests

from ._stream import ChatStream
from .errors import (
    MagnusConnectionError,
    MagnusTimeoutError,
    error_from_response,
)

__all__ = ["MagnusClient", "Conversation"]

# Content is a plain string in the simple case, or a list of multimodal parts.
Content = Union[str, List[Dict[str, Any]]]
Message = Dict[str, Any]

# Above the 60s read timeout common in reverse proxies. Matching it exactly
# means the client gives up at the same instant the proxy does, turning a clean
# server-side timeout into an ambiguous client error.
DEFAULT_TIMEOUT = 90.0

# Retried on GET always; on POST only with an Idempotency-Key, because a bare
# retried turn runs the pipeline twice.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class MagnusClient:
    """Client for a Magnus deployment.

    Args:
        base_url: the server root, e.g. ``https://api.iamagnus.com``. Not the
            ``/v1`` prefix — the health probe lives outside it.
        api_key: a System API Key or User API Key from the Magnus dashboard.
        user: end-user identifier for multi-tenant attribution, sent as the
            OpenAI ``user`` field. Overridable per call.
        timeout: seconds, per attempt.
        max_retries: extra attempts for 429/5xx and transport failures.
        auth_scheme: ``"bearer"`` (default) or ``"x-api-key"``. Both are
            accepted by the server; the header only matters behind a proxy that
            strips one of them.
        session: an existing ``requests.Session`` to reuse.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        user: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        auth_scheme: str = "bearer",
        session: Optional[requests.Session] = None,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user = user
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        if auth_scheme not in ("bearer", "x-api-key"):
            raise ValueError("auth_scheme must be 'bearer' or 'x-api-key'")
        self.auth_scheme = auth_scheme
        # A Session, so the TLS handshake is paid once rather than per turn.
        self._session = session or requests.Session()
        self._owns_session = session is None
        # Last seen rate-limit budget, for callers that pace themselves.
        self.rate_limit_remaining: Optional[int] = None
        self.rate_limit_reset: Optional[str] = None

    # ---------------------------------------------------------------- plumbing

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"iamagnus-python/{_version()}",
        }
        if self.auth_scheme == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["X-API-Key"] = self.api_key
        # Merge rather than replace: a caller passing one extra header must not
        # drop the credential.
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        return headers

    def _note_rate_limit(self, response: requests.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self.rate_limit_remaining = int(remaining)
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            self.rate_limit_reset = reset

    def _sleep_for(self, attempt: int, response: Optional[requests.Response]) -> float:
        """How long to wait before the next attempt.

        The server's own `Retry-After` wins when it sent one — guessing shorter
        just burns the next window too.
        """
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    return max(0.0, float(int(raw)))
                except ValueError:
                    pass
        # Jittered exponential backoff. Without jitter, every client that hit
        # the same 429 retries in the same instant.
        return min(8.0, (2 ** attempt) * 0.5) * (0.5 + random.random() / 2)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        stream: bool = False,
        retry: bool = False,
        timeout: Optional[float] = None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        attempts = self.max_retries + 1 if retry else 1
        last_exc: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=self._headers(headers),
                    json=json_body,
                    timeout=timeout or self.timeout,
                    stream=stream,
                )
            except requests.Timeout:
                last_exc = MagnusTimeoutError(
                    f"{method} {url} timed out after {timeout or self.timeout}s. "
                    "The turn may still have run; retry only with an Idempotency-Key."
                )
            except requests.RequestException as exc:
                last_exc = MagnusConnectionError(f"{method} {url} failed: {exc}")
            else:
                self._note_rate_limit(response)
                if response.ok:
                    return response
                if retry and response.status_code in _RETRY_STATUSES and attempt < attempts - 1:
                    delay = self._sleep_for(attempt, response)
                    response.close()
                    time.sleep(delay)
                    continue
                raise error_from_response(
                    response.status_code, _safe_json(response), response.headers
                )

            if attempt < attempts - 1:
                time.sleep(self._sleep_for(attempt, None))
                continue
            raise last_exc

        raise last_exc if last_exc else MagnusConnectionError("request failed")

    # ------------------------------------------------------------------ health

    def health(self) -> Dict[str, Any]:
        """Reachability probe — ``GET /api/health/simple``, no key needed.

        Run this before anything else when a setup is not working: it separates
        "wrong base URL" from "bad key", which otherwise both surface as a
        failure on the first chat call.
        """
        response = self._request("GET", "/api/health/simple", retry=True)
        return _safe_json(response) or {}

    # ------------------------------------------------------------------ models

    def list_agents(self) -> List[Dict[str, Any]]:
        """Agents (personas) this key can reach — ``GET /v1/models``."""
        response = self._request("GET", "/v1/models", retry=True)
        data = _safe_json(response) or {}
        return data.get("data", [])

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """One agent by id, or ``None`` if it does not exist for this key."""
        from .errors import NotFoundError

        try:
            response = self._request(
                "GET", f"/v1/models/{requests.utils.quote(agent_id, safe='')}", retry=True
            )
        except NotFoundError:
            return None
        return _safe_json(response)

    # -------------------------------------------------------------------- chat

    def _chat_payload(
        self,
        agent_id: str,
        messages: Iterable[Message],
        *,
        user: Optional[str],
        session_id: Optional[str],
        stream: bool,
        include_usage: bool,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": agent_id, "messages": list(messages)}
        effective_user = user if user is not None else self.user
        if effective_user is not None:
            payload["user"] = effective_user
        if session_id is not None:
            # Rejected by the server with a 400 if it is not a UUID; caught here
            # so the failure names the client's own bug instead of a round trip.
            _require_uuid(session_id)
            payload["session_id"] = session_id
        if stream:
            payload["stream"] = True
            if include_usage:
                payload["stream_options"] = {"include_usage": True}
        if extra_body:
            # Forward compatibility: a server field newer than this SDK can be
            # sent without waiting for a release. Nothing here is validated —
            # that is the point, and why it is not the ordinary path.
            payload.update(extra_body)
        return payload

    def chat(
        self,
        agent_id: str,
        messages: Iterable[Message],
        *,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        user: Optional[str] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run one turn and return the whole OpenAI-shaped response.

        ``choices[0].message.content`` is the answer;
        ``magnus.session_id`` is the conversation to carry into the next turn.

        Passing ``idempotency_key`` makes the turn safe to retry: the server
        replays its first response instead of running the pipeline again. It is
        also what allows this client to retry a failed POST at all.
        """
        payload = self._chat_payload(
            agent_id, messages, user=user, session_id=session_id,
            stream=False, include_usage=False, extra_body=extra_body,
        )
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = self._request(
            "POST", "/v1/chat/completions",
            json_body=payload, headers=headers,
            retry=bool(idempotency_key),
        )
        return _safe_json(response) or {}

    def stream_chat(
        self,
        agent_id: str,
        messages: Iterable[Message],
        *,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
        include_usage: bool = False,
        extra_body: Optional[Mapping[str, Any]] = None,
        on_finish: Optional[Callable[[ChatStream], None]] = None,
    ) -> ChatStream:
        """Run one turn, delivered as it is generated.

        Iterate the returned :class:`~iamagnus._stream.ChatStream` for text
        deltas; when it is exhausted, ``.text``, ``.usage``, ``.session_id`` and
        ``.magnus`` hold the same values a buffered call would have returned.

        No ``idempotency_key``: a streamed body cannot be replayed, so the
        server releases the key and a retry re-runs the turn. Use :meth:`chat`
        when a turn must not run twice.
        """
        payload = self._chat_payload(
            agent_id, messages, user=user, session_id=session_id,
            stream=True, include_usage=include_usage, extra_body=extra_body,
        )
        response = self._request(
            "POST", "/v1/chat/completions",
            json_body=payload,
            headers={"Accept": "text/event-stream"},
            stream=True,
            # A stream cannot be replayed and has no idempotency key, so a
            # retry would silently run the turn a second time.
            retry=False,
            # The read timeout applies between chunks, not to the whole turn;
            # a long answer must not trip it.
            timeout=self.timeout,
        )
        return ChatStream(response.iter_lines(decode_unicode=True), on_finish=on_finish)

    def send_message(
        self,
        agent_id: str,
        content: Content,
        *,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
        history: Optional[List[Message]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """One user message in, the answer's text out.

        ``history`` is accepted for OpenAI-shaped call sites, but it does not
        restore a thread — the server reads only the last user message. Use
        :meth:`conversation` for continuity.
        """
        messages = list(history or [])
        messages.append({"role": "user", "content": content})
        response = self.chat(
            agent_id, messages,
            session_id=session_id, user=user, idempotency_key=idempotency_key,
        )
        return _first_text(response)

    def conversation(
        self, agent_id: str, *, user: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> "Conversation":
        """Open a thread that carries its ``session_id`` across turns.

        Prefer this over :meth:`send_message` with ``history``: the server keeps
        memory server-side and identifies the thread by session id. Without one,
        continuity falls back to a time window and is lost silently when it
        expires.
        """
        return Conversation(
            self, agent_id,
            user=user if user is not None else self.user,
            session_id=session_id,
        )

    # ------------------------------------------------------------------ teardown

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "MagnusClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Conversation:
    """A thread with an agent, identified by its session id.

    The server reads only the last user message and keeps the conversation's
    memory and state server-side; the thread is the session id, not the
    history a client resends.
    """

    def __init__(
        self,
        client: MagnusClient,
        agent_id: str,
        *,
        user: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        if session_id is not None:
            _require_uuid(session_id)
        self._client = client
        self._agent_id = agent_id
        self._user = user
        self.session_id: Optional[str] = session_id
        self.session_source: Optional[str] = None
        self.last_trace_id: Optional[str] = None
        self.last_turn_id: Optional[str] = None
        self.last_usage: Optional[Dict[str, int]] = None
        self.last_usage_source: Optional[str] = None

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def send(self, content: Content, *, idempotency_key: Optional[str] = None) -> str:
        """Run one turn and return the answer's text."""
        response = self._client.chat(
            self._agent_id,
            [{"role": "user", "content": content}],
            session_id=self.session_id,
            idempotency_key=idempotency_key,
            user=self._user,
        )
        self._adopt(
            response.get("magnus") if isinstance(response.get("magnus"), dict) else {},
            response.get("session_id"),
            response.get("usage"),
        )
        return _first_text(response)

    def stream(self, content: Content, *, include_usage: bool = False) -> ChatStream:
        """Run one turn, delivered as it is generated.

        The session is adopted when the stream finishes — including when it
        raises, since a turn that failed mid-stream still ran and still moved
        the conversation.
        """
        def _finish(stream: ChatStream) -> None:
            self._adopt(stream.magnus, stream.session_id, stream.usage)

        return self._client.stream_chat(
            self._agent_id,
            [{"role": "user", "content": content}],
            session_id=self.session_id,
            user=self._user,
            include_usage=include_usage,
            on_finish=_finish,
        )

    def reset(self) -> None:
        """Forget the session id, so the next turn opens a new conversation."""
        self.session_id = None
        self.session_source = None

    def _adopt(
        self,
        magnus: Mapping[str, Any],
        fallback_session_id: Optional[str],
        usage: Optional[Dict[str, int]],
    ) -> None:
        # The pipeline may roll the session mid-turn (persona, user or org
        # change), so the id the server says it ran on wins over the one sent.
        magnus = magnus or {}
        self.session_id = (
            magnus.get("session_id") or fallback_session_id or self.session_id
        )
        self.session_source = magnus.get("session_source") or self.session_source
        self.last_trace_id = magnus.get("trace_id")
        self.last_turn_id = magnus.get("turn_id")
        self.last_usage_source = magnus.get("usage_source")
        if isinstance(usage, dict):
            self.last_usage = usage


# ---------------------------------------------------------------------- helpers


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _require_uuid(value: str) -> None:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"session_id must be a UUID, got {value!r}. Magnus rejects anything "
            "else with a 400 rather than silently starting a new conversation."
        ) from None


def _first_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("Magnus returned no choices for this turn.")
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _version() -> str:
    from . import __version__

    return __version__

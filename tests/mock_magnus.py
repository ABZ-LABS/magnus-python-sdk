"""A fake Magnus that implements CONTRACT.md.

The unit suite runs against this rather than against `requests-mock`, so the
tests exercise real sockets, real chunked transfer and real SSE framing — the
three things a mocked transport cannot reproduce.

Every behaviour here is specified in CONTRACT.md. When the contract changes,
change CONTRACT.md first, then this file, and let the suite fail.
"""
import json
import threading
import uuid
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

UNSUPPORTED_PARAMS = ("tools", "tool_choice", "functions", "function_call", "response_format")

AGENTS = ["magnus_standard", "porteria", "inmobiliaria"]


def _model_object(model_id: str) -> Dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 1757000000,
        "owned_by": "magnus",
        "permission": [],
        "root": model_id,
        "parent": None,
    }


def _error(message: str, *, type_: str = "invalid_request_error",
           param: Optional[str] = None, code: Optional[str] = None) -> Dict[str, Any]:
    return {"error": {"message": message, "type": type_, "param": param, "code": code}}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Silence the per-request stderr line; a failing test prints the recording.
    def log_message(self, *args):
        pass

    def handle_one_request(self):
        """A client hanging up mid-body is normal here, not a server fault.

        Closing a stream before draining it — which is exactly what the
        mid-stream-error tests do — resets the connection. Left alone,
        socketserver prints a traceback per test and buries the real output.
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def finish(self):
        try:
            super().finish()
        except (ConnectionResetError, BrokenPipeError):
            pass

    # ------------------------------------------------------------- utilities

    @property
    def state(self) -> "MockMagnus":
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_sse(self, chunks: List[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            data = chunk.encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization")
        presented = None
        if auth and auth.startswith("Bearer ") and auth[7:]:
            presented = auth[7:]
        elif self.headers.get("X-API-Key"):
            presented = self.headers["X-API-Key"]
        if presented is None:
            return False
        # `api_key = None` accepts any non-empty credential, which is what most
        # tests want; setting it makes the mock check, so a test can present a
        # key that is real-looking and still wrong.
        expected = self.state.api_key
        return expected is None or presented == expected

    def _record(self, method: str, path: str, body: Any):
        self.state.requests.append({
            "method": method,
            "path": path,
            "body": body,
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })

    # ------------------------------------------------------------------- GET

    def do_GET(self):
        self._record("GET", self.path, None)

        if self.path == "/api/health/simple":
            return self._send_json(200, {
                "status": "ok",
                "timestamp": "2026-09-09T12:00:00+00:00",
                "version": {"version": "mock"},
            })

        if not self._authorized():
            return self._send_json(401, _error(
                "You didn't provide an API key.", code=None,
            ))

        forced = self.state.take_forced_response()
        if forced is not None:
            status, body, headers = forced
            return self._send_json(status, body, headers)

        if self.path == "/v1/models":
            return self._send_json(200, {
                "object": "list",
                "data": [_model_object(a) for a in self.state.agents],
            })

        if self.path.startswith("/v1/conversations/updates") and self.state.serves_updates:
            return self._conversation_updates()

        if self.path.startswith("/v1/models/"):
            model_id = self.path[len("/v1/models/"):]
            if model_id == "magnus" and "magnus_standard" in self.state.agents:
                model_id = "magnus_standard"
            if model_id in self.state.agents:
                return self._send_json(200, _model_object(model_id))
            return self._send_json(404, _error(
                f"The model '{model_id}' does not exist or you do not have access.",
                param="model", code="model_not_found",
            ))

        return self._send_json(404, _error("Not found."))

    def _conversation_updates(self):
        """The operator's replies after a cursor, and whether a person owns it."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        if self.state.before_updates is not None:
            self.state.before_updates(self.state)
        messages = self.state.operator_messages
        start = 0
        if query.get("after"):
            ids = [m["id"] for m in messages]
            if query["after"] not in ids:
                return self._send_json(400, _error(
                    "after is not a message of this user.",
                    param="after", code="invalid_cursor",
                ))
            start = ids.index(query["after"]) + 1
        page = messages[start:start + self.state.updates_page]
        return self._send_json(200, {
            "object": "list",
            "handoff": bool(self.state.handoff),
            "data": page,
            "has_more": start + len(page) < len(messages),
        })

    # ------------------------------------------------------------------ POST

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        self._record("POST", self.path, body)

        if self.path != "/v1/chat/completions":
            return self._send_json(404, _error("Not found."))

        if not self._authorized():
            return self._send_json(401, _error(
                "Incorrect API key provided.", code="invalid_api_key",
            ))

        forced = self.state.take_forced_response()
        if forced is not None:
            status, forced_body, headers = forced
            return self._send_json(status, forced_body, headers)

        if not isinstance(body, dict):
            return self._send_json(400, _error(
                "Request body must be a JSON object.", param="body",
            ))

        # Refused rather than ignored — the pipeline owns these.
        for param in UNSUPPORTED_PARAMS:
            if body.get(param) in (None, [], {}):
                continue
            return self._send_json(400, _error(
                f"'{param}' is not supported by this endpoint.",
                param=param, code="unsupported_parameter",
            ))
        if body.get("n") is not None and body.get("n") != 1:
            return self._send_json(400, _error(
                "'n' must be 1.", param="n", code="unsupported_parameter",
            ))

        messages = body.get("messages")
        if not messages or not isinstance(messages, list):
            return self._send_json(400, _error(
                "messages is required and must be a list.", param="messages",
            ))

        last_user_text = _last_user_text(messages)
        if not last_user_text:
            return self._send_json(400, _error(
                "No user message with text content found.", param="messages",
            ))

        session_id = body.get("session_id") or self.headers.get("X-Magnus-Session-Id")
        if session_id:
            try:
                uuid.UUID(str(session_id))
            except (ValueError, AttributeError, TypeError):
                return self._send_json(400, _error(
                    "session_id must be a UUID.", param="session_id",
                ))
            source = "explicit"
        else:
            source = "new"

        # The pipeline may roll the session mid-turn; the mock does it on demand
        # so a client that echoes the request value instead of the response one
        # is caught.
        effective_session = self.state.rolled_session_id or session_id or str(uuid.uuid4())
        if self.state.rolled_session_id:
            source = "new"

        idem_key = self.headers.get("Idempotency-Key")
        if idem_key:
            stored = self.state.idempotency.get(idem_key)
            if stored == "IN_FLIGHT":
                return self._send_json(409, _error(
                    "A request with this Idempotency-Key is still in progress.",
                    param="Idempotency-Key", code="request_in_progress",
                ))
            if stored is not None:
                status, cached = stored
                return self._send_json(status, cached)

        self.state.turns_run += 1
        text = self.state.reply_for(last_user_text)
        extensions = {
            "session_id": effective_session,
            "magnus": {
                "session_id": effective_session,
                "session_source": source,
                "trace_id": self.state.trace_id,
                "turn_id": self.state.turn_id,
                "usage_source": self.state.usage_source,
                # None stands for a server older than the field: it is omitted.
                **({} if self.state.handoff is None else {"handoff": self.state.handoff}),
            },
        }
        usage = dict(self.state.usage)

        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            return self._send_sse(
                self.state.build_stream(text, extensions, usage, include_usage)
            )

        response = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 1757000000,
            "model": body.get("model", "magnus_standard"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": usage,
            **extensions,
        }
        if idem_key:
            self.state.idempotency[idem_key] = (200, response)
        return self._send_json(200, response, {
            "X-RateLimit-Remaining": str(self.state.rate_limit_remaining),
            "X-RateLimit-Reset": self.state.rate_limit_reset,
        })


def _last_user_text(messages: List[Any]) -> str:
    """The server reads only the last user message, flattening multimodal parts."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                part.get("text") or ""
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            if text.strip():
                return text
    return ""


class MockMagnus:
    """A Magnus deployment that exists for the length of one test."""

    def __init__(self):
        self.agents = list(AGENTS)
        # None accepts any non-empty key; set it to enforce one.
        self.api_key: Optional[str] = None
        self.requests: List[Dict[str, Any]] = []
        self.idempotency: Dict[str, Any] = {}
        self.turns_run = 0
        self.reply = "Hola, soy Magnus."
        self.trace_id = "trace-1"
        self.turn_id = "turn-1"
        self.usage_source = "measured"
        # True stands for a conversation a person from the team has taken over.
        self.handoff = False
        # What GET /v1/conversations/updates serves: the operator's replies,
        # oldest first, and a hook a test uses to change state between polls.
        self.operator_messages: List[Dict[str, Any]] = []
        self.updates_page = 50
        self.before_updates = None
        # False stands for a server older than the endpoint: it answers 404.
        self.serves_updates = True
        self.usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        self.rate_limit_remaining = 119
        self.rate_limit_reset = "2026-09-09T12:01:00+00:00"
        self.rolled_session_id: Optional[str] = None
        # Queue of (status, body, headers) served before normal handling, so a
        # test can stage a 429 followed by a success.
        self._forced: List[Any] = []
        # How a streamed turn is delivered. "tokens" = word by word (the live
        # path), "single" = one delta (the server delivered the turn whole),
        # "error" = a failure after the stream opened.
        self.stream_mode = "tokens"
        self.stream_error = {
            "message": "Internal server error.",
            "type": "server_error",
            "param": None,
            "code": None,
        }

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        # Without this, shutdown blocks on every keep-alive connection the
        # suite left open, turning teardown into half the wall clock.
        self._server.daemon_threads = True
        self._server.state = self  # type: ignore[attr-defined]
        # A 0.05s poll rather than the 0.5s default: `shutdown()` waits up to
        # one interval, which at the default is most of the suite's runtime.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def reply_for(self, _user_text: str) -> str:
        return self.reply

    def force(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None):
        """Serve this next, once, before normal handling."""
        self._forced.append((status, body, headers or {}))

    def take_forced_response(self):
        return self._forced.pop(0) if self._forced else None

    def build_stream(self, text, extensions, usage, include_usage) -> List[str]:
        def frame(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        def chunk(choices, extra=None, usage_payload=None):
            payload = {
                "id": "chatcmpl-mock", "object": "chat.completion.chunk",
                "created": 1757000000, "model": "magnus_standard", "choices": choices,
            }
            if usage_payload is not None:
                payload["usage"] = usage_payload
            if extra:
                payload.update(extra)
            return frame(payload)

        out: List[str] = []

        if self.stream_mode == "single":
            # The buffered path: one delta, extensions riding on it.
            out.append(chunk(
                [{"index": 0, "delta": {"role": "assistant", "content": text},
                  "finish_reason": None}],
                extra=extensions,
            ))
            out.append(chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}]))
        else:
            # The live path: an opening chunk before any token, then tokens,
            # then a closing chunk carrying the extensions.
            out.append(chunk([{"index": 0, "delta": {"role": "assistant"},
                               "finish_reason": None}]))
            if self.stream_mode == "error":
                out.append(chunk([{"index": 0, "delta": {"content": text[:5]},
                                   "finish_reason": None}]))
                out.append(chunk(
                    [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    extra={"error": self.stream_error},
                ))
                out.append("data: [DONE]\n\n")
                return out
            for piece in _tokenize(text):
                out.append(chunk([{"index": 0, "delta": {"content": piece},
                                   "finish_reason": None}]))
            out.append(chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}],
                             extra=extensions))

        if include_usage:
            out.append(chunk([], usage_payload=usage))
        out.append("data: [DONE]\n\n")
        return out

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _tokenize(text: str) -> List[str]:
    """Split into the kind of pieces the server streams: words, spaces kept."""
    parts = text.split(" ")
    return [p if i == 0 else " " + p for i, p in enumerate(parts)]


def operator_message(content: str, created: int = 1767225600) -> Dict[str, Any]:
    """One reply a person from the team wrote, as the server serves it."""
    return {"id": str(uuid.uuid4()), "object": "conversation.message",
            "author": "human", "content": content, "created": created}

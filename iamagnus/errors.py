"""Typed errors for the Magnus API.

The server always answers a failure with the same envelope::

    {"error": {"message": ..., "type": ..., "param": ..., "code": ...}}

Raising a bare ``HTTPError`` threw that away and left the caller reading a
status code, so ``tools is not supported`` and ``your key expired`` arrived
looking identical. Every field of the envelope survives onto the exception.
"""
from typing import Any, Dict, Mapping, Optional


class MagnusError(Exception):
    """Base class for every error this SDK raises."""


class MagnusConnectionError(MagnusError):
    """The request never reached Magnus, or the connection died mid-flight."""


class MagnusTimeoutError(MagnusConnectionError):
    """The request was still open when the client's timeout expired.

    A timeout is not an answer: the turn may well have run. Retrying it without
    an ``Idempotency-Key`` can run the pipeline a second time and duplicate
    whatever side effects its tools have.
    """


class MagnusAPIError(MagnusError):
    """Magnus answered with an error envelope."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        error_type: Optional[str] = None,
        code: Optional[str] = None,
        param: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Optional[Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.type = error_type
        self.code = code
        self.param = param
        self.headers: Dict[str, str] = dict(headers or {})
        self.body = body

    @property
    def retry_after(self) -> Optional[int]:
        """Seconds the server asked us to wait, when it said."""
        raw = self.headers.get("Retry-After") or self.headers.get("retry-after")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def __str__(self) -> str:
        bits = [f"{self.status}"]
        if self.code:
            bits.append(self.code)
        if self.param:
            bits.append(f"param={self.param}")
        return f"[{' '.join(bits)}] {self.message}"


class AuthenticationError(MagnusAPIError):
    """401 — no key, or a key Magnus does not accept."""


class PermissionDeniedError(MagnusAPIError):
    """403 — the key is valid but not for this."""


class NotFoundError(MagnusAPIError):
    """404 — no such agent, or no access to it."""


class InvalidRequestError(MagnusAPIError):
    """400 — the request cannot be honoured as written."""


class UnsupportedParameterError(InvalidRequestError):
    """400 with ``code: unsupported_parameter``.

    Magnus runs its own agent pipeline: tools are configured per agent and the
    response format is the agent's decision, so ``tools``, ``tool_choice``,
    ``functions``, ``function_call``, ``response_format`` and ``n > 1`` are
    refused rather than silently ignored. ``.param`` names the offender.
    """


class ConflictError(MagnusAPIError):
    """409 — a turn with this ``Idempotency-Key`` is still running."""


class RateLimitError(MagnusAPIError):
    """429 — the key's window is exhausted. See ``.retry_after``."""


class ServerError(MagnusAPIError):
    """5xx — Magnus failed to process the turn."""


class StreamError(MagnusError):
    """A streamed turn failed after the stream had already opened.

    The status line went out as 200 with the first chunk and cannot be taken
    back, so the failure arrives inside the stream instead. Whatever was
    streamed before it is kept on ``.partial_text`` — it is what the reader has
    already seen — but the turn did not succeed.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: Optional[str] = None,
        code: Optional[str] = None,
        param: Optional[str] = None,
        partial_text: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.type = error_type
        self.code = code
        self.param = param
        self.partial_text = partial_text

    def __str__(self) -> str:
        return f"[stream {self.code or self.type or 'error'}] {self.message}"


_STATUS_MAP = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def error_from_response(
    status: int,
    body: Any,
    headers: Optional[Mapping[str, str]] = None,
) -> MagnusAPIError:
    """Build the right exception from an HTTP failure."""
    envelope = body.get("error") if isinstance(body, dict) else None
    if isinstance(envelope, dict):
        message = str(envelope.get("message") or f"Magnus returned {status}.")
        error_type = envelope.get("type")
        code = envelope.get("code")
        param = envelope.get("param")
    elif isinstance(envelope, str):
        message, error_type, code, param = envelope, None, None, None
    else:
        # Not an envelope at all — a proxy error page, or HTML from the wrong
        # host. Keep a slice of it: "502" alone never located anything.
        snippet = str(body)[:200].strip() if body else ""
        message = f"Magnus returned {status}." + (f" {snippet}" if snippet else "")
        error_type = code = param = None

    if code == "unsupported_parameter":
        cls = UnsupportedParameterError
    elif status >= 500:
        cls = ServerError
    else:
        cls = _STATUS_MAP.get(status, MagnusAPIError)

    return cls(
        message,
        status=status,
        error_type=error_type,
        code=code,
        param=param,
        headers=headers,
        body=body,
    )

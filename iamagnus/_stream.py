"""SSE parsing for streamed turns.

Magnus streams an OpenAI-shaped `text/event-stream`. Two things about it are
not OpenAI's and are the reason this is a module and not four lines inline:

- the Magnus extensions (`session_id`, `magnus`) ride on whichever chunk
  carries them — the first content chunk when the turn was buffered, the
  closing chunk when it streamed — so they are merged as they arrive rather
  than read from a fixed position;
- a turn that fails after the stream opened reports it *inside* the stream,
  with HTTP 200 already sent. That chunk is the difference between a truncated
  answer and a raised error.
"""
import json
from typing import Any, Dict, Iterator, List, Optional

from .errors import StreamError


def iter_sse_data(lines: Iterator[str]) -> Iterator[str]:
    """Yield the payload of each `data:` frame, stopping at `[DONE]`.

    Blank lines separate frames and comment lines (`:`) are keep-alives; both
    are skipped. Anything else is not SSE and is ignored rather than crashing a
    turn that is otherwise fine.
    """
    for raw in lines:
        if raw is None:
            continue
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        yield payload


class ChatStream:
    """A turn arriving as it is generated.

    Iterating yields text deltas as they arrive. Once iteration finishes, the
    whole turn is available on the attributes below — the same fields a
    buffered response carries.
    """

    def __init__(self, lines: Iterator[str], *, on_finish=None):
        self._lines = lines
        self._on_finish = on_finish
        self._closed = False
        self.text: str = ""
        self.usage: Optional[Dict[str, int]] = None
        self.session_id: Optional[str] = None
        self.magnus: Dict[str, Any] = {}
        self.id: Optional[str] = None
        self.model: Optional[str] = None
        self.finish_reason: Optional[str] = None
        self.chunks: List[Dict[str, Any]] = []

    def __iter__(self) -> Iterator[str]:
        for payload in iter_sse_data(self._lines):
            try:
                chunk = json.loads(payload)
            except ValueError:
                # A frame we cannot parse is not worth failing a turn over, but
                # silently dropping it would hide a server-side format change.
                continue
            if not isinstance(chunk, dict):
                continue
            self.chunks.append(chunk)

            self.id = chunk.get("id") or self.id
            self.model = chunk.get("model") or self.model

            # Extensions can be on any chunk; merge, never overwrite with None.
            if chunk.get("session_id"):
                self.session_id = chunk["session_id"]
            if isinstance(chunk.get("magnus"), dict):
                self.magnus.update(chunk["magnus"])
                if chunk["magnus"].get("session_id"):
                    self.session_id = chunk["magnus"]["session_id"]
            if isinstance(chunk.get("usage"), dict):
                self.usage = chunk["usage"]

            # A turn that failed after the stream opened. Raised, not returned:
            # the alternative is handing back a truncated answer as a success.
            error = chunk.get("error")
            if isinstance(error, dict):
                self._finish()
                raise StreamError(
                    str(error.get("message") or "The turn failed mid-stream."),
                    error_type=error.get("type"),
                    code=error.get("code"),
                    param=error.get("param"),
                    partial_text=self.text,
                )

            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                if choice.get("finish_reason"):
                    self.finish_reason = choice["finish_reason"]
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                piece = delta.get("content")
                if piece:
                    self.text += piece
                    yield piece

        self._finish()

    def close(self) -> None:
        self._finish()

    def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._lines, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - best effort
                pass
        if self._on_finish is not None:
            self._on_finish(self)

    def __enter__(self) -> "ChatStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

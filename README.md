# Magnus SDK for Python

The official Python client for **[Magnus Core](https://core.iamagnus.com)**:
governed AI agents behind an OpenAI-compatible API. The model understands and
writes; rules decide what happens, anything irreversible gets confirmed first,
and every turn leaves a trace.

```bash
pip install iamagnus
```

Python 3.8+. One dependency: `requests`.

```python
from iamagnus import MagnusClient

with MagnusClient("https://api.iamagnus.com", "magnus_sys_...") as client:
    agent = client.list_agents()[0]["id"]

    # A thread: the session id continues it, not resent history.
    chat = client.conversation(agent, user="jane@company.com")

    print(chat.send("Hi, what can you do?"))
    print(chat.send("And the price?"))
```

## Getting an API key

1. In the Magnus dashboard, open **Configuration → System API Keys**.
2. Create a key with a descriptive name and the `api_generic` channel. The
   channel is stamped on every turn the key runs, so prefer one key per
   integration over one shared key.
3. **Copy it right away.** Magnus stores only a hash and shows the key once.

Keys start with `magnus_sys_`.

> **Not to be confused with "LLM API Keys".** That screen holds *your* OpenAI,
> Anthropic or other provider credentials, so that Magnus can call models on your
> behalf. They do not authenticate you against Magnus; using one here gives a
> 401.

## Pointing the client at a deployment

The base URL is configuration, not a constant: nothing about `iamagnus.com` is
built into the library. Pass the **server root**, without `/v1` — the client
builds `/v1/...` itself, plus `/api/health/simple`, which lives outside that
prefix.

```python
hosted = MagnusClient("https://api.iamagnus.com", "magnus_sys_...")
local  = MagnusClient("http://localhost:5001",    "magnus_sys_...")

# In a deployment, read both from the environment:
import os
client = MagnusClient(os.environ["MAGNUS_BASE_URL"], os.environ["MAGNUS_API_KEY"])
```

A trailing slash is trimmed, and an empty URL fails at construction rather than
as an unreadable transport error.

### Checking the URL and the key separately

```python
client.health()        # no key involved: proves the URL is right
client.list_agents()   # uses the key: proves the credential
```

If `health()` works and `list_agents()` returns 401, the key is the problem, not
the URL — and the other way round.

## Three things that are not OpenAI

**History is not state.** The server reads only the last user message and keeps
the conversation's memory and state server-side. Resending history does not
restore a thread — a session id does. Use `conversation()`; without one,
continuity falls back to a time window and is lost silently when it expires.

**The agent owns the turn.** `tools`, `tool_choice`, `functions`,
`function_call`, `response_format` and `n > 1` are *refused*, not ignored: tools
are configured per agent and the response format is the agent's decision.
`temperature`, `max_tokens`, `top_p`, `stop`, `seed` and `presence_penalty` are
accepted and ignored — the agent owns them too.

**A streamed turn can fail after HTTP 200.** Once the first chunk is out the
status line cannot be taken back, so a failure arrives *inside* the stream. This
client raises `StreamError` rather than handing back a truncated answer as a
success.

## Streaming

```python
stream = chat.stream("Tell me more")

for delta in stream:
    print(delta, end="", flush=True)

print(stream.text, stream.session_id, stream.magnus["usage_source"])
```

Two shapes are normal and both are handled: token by token, and a single delta
for a turn the server delivers whole. `include_usage=True` adds the final chunk
carrying `stream.usage`.

A turn that fails mid-stream raises out of the loop:

```python
from iamagnus import StreamError

try:
    for delta in stream:
        print(delta, end="")
except StreamError as error:
    # error.partial_text is what the reader already saw
    print(error.code, error.message)
```

## Errors

Every failure carries the server's error envelope:

```python
from iamagnus import AuthenticationError, RateLimitError, UnsupportedParameterError

try:
    client.chat(agent, messages)
except RateLimitError as error:
    time.sleep(error.retry_after or 5)
except UnsupportedParameterError as error:
    print(f"Magnus refuses {error.param!r}")
except AuthenticationError:
    raise SystemExit("the API key is not accepted")
```

| Class | Status |
|---|---|
| `InvalidRequestError` | 400 |
| `UnsupportedParameterError` | 400, `code: unsupported_parameter` (subclass of the above) |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `ConflictError` | 409, a turn with this `Idempotency-Key` is still running |
| `RateLimitError` | 429, see `.retry_after` |
| `ServerError` | 5xx |
| `MagnusConnectionError` / `MagnusTimeoutError` | never reached Magnus, or gave up waiting |
| `StreamError` | the turn failed after the stream opened |

All subclass `MagnusError`. `.status`, `.type`, `.code`, `.param`, `.headers`
and `.message` carry the server's own words.

## Retries and idempotency

A turn advances the conversation and can run tools with side effects, so
retrying one blindly can duplicate them. This client therefore retries:

- **GET** always, on 429/5xx and transport failures;
- **POST** only when you passed an `idempotency_key`, because the server then
  replays its first response instead of running the turn again;
- **never a stream** — a streamed body cannot be replayed.

`Retry-After` is honoured; otherwise the backoff is exponential with jitter.

```python
import uuid
client.chat(agent, messages, idempotency_key=str(uuid.uuid4()))
```

## Metering

`response["usage"]` holds real provider token counts when
`response["magnus"]["usage_source"] == "measured"`. `"estimated"` means the turn
never reached an LLM and the numbers are a `len/4` heuristic. **Do not bill on
an estimate.**

`client.rate_limit_remaining` tracks the last seen budget for the key.

## Multi-tenancy

`user="jane@company.com"` on the client, the conversation, or a single call. It
sets the OpenAI `user` field, which Magnus uses to attribute the turn to an end
user inside the API key's organization.

## Verifying a deployment

`magnus-livecheck` runs the fourteen checks in [CONTRACT.md](CONTRACT.md)
against a real deployment and exits non-zero unless all of them pass:

```bash
export MAGNUS_BASE_URL=https://api.iamagnus.com
export MAGNUS_API_KEY=magnus_sys_...
export MAGNUS_AGENT=magnus_standard   # optional: defaults to the first agent listed

magnus-livecheck
```

Checks 5, 8, 9, 10 and 13 run real turns, which cost tokens and are recorded
like any other conversation — point it at a test agent with `--agent`.

## API

| | |
|---|---|
| `MagnusClient(base_url, api_key, *, user, timeout, max_retries, auth_scheme, session)` | |
| `health()` | unauthenticated reachability probe |
| `list_agents()` / `get_agent(id)` | agents; an unknown id is `None` |
| `chat(agent, messages, *, session_id, idempotency_key, user, extra_body)` | one buffered turn |
| `stream_chat(agent, messages, *, session_id, user, include_usage)` | one streamed turn |
| `send_message(agent, content, ...)` | text in, text out |
| `conversation(agent, *, user, session_id)` | a thread: `.send()`, `.stream()`, `.reset()` |

`extra_body` forwards server fields newer than this library. Every wire detail
is in [CONTRACT.md](CONTRACT.md).

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite runs against a fake Magnus that implements [CONTRACT.md](CONTRACT.md)
over real sockets, so SSE framing and chunked transfer are exercised for real.
Releases are described in [RELEASING.md](RELEASING.md).

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

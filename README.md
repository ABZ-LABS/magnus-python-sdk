# Magnus SDK for Python

**English** · [Español](README.es.md)

The official Python client for **[Magnus Core](https://core.iamagnus.com)**:
governed AI agents behind an OpenAI-compatible API. The model understands and
writes; the agent's rules decide what happens and which actions wait for a
confirmation, and every turn leaves a trace.

```bash
pip install iamagnus
```

Python 3.9+. One dependency: `requests`. If `pip install iamagnus` fails, the
same package installs straight from GitHub: see
[Installing without PyPI](#installing-without-pypi).

```python
from iamagnus import MagnusClient

with MagnusClient("https://app.iamagnus.com", "magnus_sys_...") as client:
    agent = client.list_agents()[0]["id"]

    # One thread per end user: `user` continues it, not resent history.
    chat = client.conversation(agent, user="jane@company.com")

    print(chat.send("Hi, what can you do?"))
    print(chat.send("And the price?"))
```

## Installing without PyPI

Use this when the package is not on PyPI, or the machine cannot reach it. The
code is the same and so is the import, `from iamagnus import MagnusClient`.

**From GitHub.** pip builds the package from a tag of this repository. It needs
`git` on the machine:

```bash
pip install "iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0"
```

The same line works in `requirements.txt`, and other tools take the same URL:

```text
# requirements.txt
iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0
```

```bash
uv add "iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0"
poetry add "git+https://github.com/ABZ-LABS/magnus-python-sdk.git#v0.1.0"
```

Pin a tag, as above, so every build installs the same code. `@main` follows
the latest commit, which is not a release.

**Without network access to GitHub** (a closed CI, a customer's network). Build
a wheel once on a machine that has access, and ship the file with the project:

```bash
git clone --branch v0.1.0 https://github.com/ABZ-LABS/magnus-python-sdk
pip wheel ./magnus-python-sdk --no-deps -w vendor/
# vendor/iamagnus-0.1.0-py3-none-any.whl goes into the project

pip install vendor/iamagnus-0.1.0-py3-none-any.whl
```

The wheel does not bundle `requests`, which still comes from your package
index. If there is no index at all, download it next to the wheel with
`pip download requests -d vendor/` and install with
`pip install --no-index --find-links vendor/ iamagnus`. Run `pip download` on
the same operating system and Python version as the target: some of the
`requests` dependencies are built per platform.

## Getting an API key

1. In the Magnus dashboard, open **System API Keys**, under *Integration keys*
   in the sidebar. Organization admins see it.
2. **Create key**, and choose **which agent should answer**. A key is created
   for one agent and always answers as that agent: `list_agents()` returns exactly
   that one, and naming another agent of your organization is refused with
   `model_not_allowed`. Keys for your own agents need a paid plan; the sample
   agents are open on every plan.
3. Under **What will use this key?**, keep **My app or backend**. That choice is
   stamped on every turn the key runs, so prefer one key per integration over
   one shared key.
4. **Copy it right away.** Magnus stores only a hash and shows the key once.

Keys start with `magnus_sys_` (`magnus_gpt_` for a key made for a chat client
such as OpenWebUI).

> **Not to be confused with "LLM API Keys".** That screen holds *your* OpenAI,
> Anthropic or other provider credentials, so that Magnus can call models on your
> behalf. They do not authenticate you against Magnus; using one here gives a
> 401.

## Pointing the client at a deployment

The base URL is configuration, not a constant: nothing about `iamagnus.com` is
built into the library. The hosted service is `https://app.iamagnus.com`, the
same address as the dashboard. Pass the **server root**, without `/v1` — the
client builds `/v1/...` itself, plus `/api/health/simple`, which lives outside
that prefix.

```python
hosted = MagnusClient("https://app.iamagnus.com", "magnus_sys_...")
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

## What is different from OpenAI

**The key picks the agent.** `model` does not choose who answers; the key's
agent does. Naming another agent of the organization is refused
(`model_not_allowed`), and any other value, such as `gpt-4o`, is ignored, so an
OpenAI client works unchanged.

**A thread is the end user, not the history.** The server reads only the last
user message and keeps the conversation's memory and state server-side, so
resending history restores nothing. There is one live thread per (API key,
`user`, agent): the same `user` continues it, and it ends after 30 idle
minutes. **Always pass `user`**: without it, everyone calling through the key
shares one thread. Each response reports the session the server ran on, but
sending a session id back cannot select, resume or reset a thread.

**The agent owns the turn.** `tools`, `tool_choice`, `functions`,
`function_call`, `response_format` and `n > 1` are *refused*, not ignored: tools
are configured per agent and the response format is the agent's decision.
`temperature`, `max_tokens`, `top_p`, `stop`, `seed` and `presence_penalty` are
accepted and ignored — the agent owns them too. Some OpenAI clients send
`tool_choice: "auto"` or `response_format: {"type": "text"}` by default; those
count as set and are refused, so strip them.

**Some limits answer 200.** When an end user, the organization or its plan runs
out of turns, the turn returns HTTP 200 with a sentence instead of an answer,
`usage_source: "estimated"` and no trace id, not a 429. The list is in
[CONTRACT.md](CONTRACT.md#limits-that-answer-200).

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
import time

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
| `InvalidRequestError` | 400, including `code: model_not_allowed` (the key belongs to another agent) |
| `UnsupportedParameterError` | 400, `code: unsupported_parameter` (subclass of the above) |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403, the key's organization is missing or deactivated |
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

Use a fresh UUID for each turn: the server matches the key across the whole
organization for 24 hours, without looking at the body or the end user.

## Metering

`response["usage"]` holds real provider token counts when
`response["magnus"]["usage_source"] == "measured"`. `"estimated"` means the turn
never reached an LLM, which includes the limits that answer 200, and the numbers
are a `len/4` heuristic. **Do not bill on an estimate.**

`client.rate_limit_remaining` tracks the last seen budget for the key.

## Multi-tenancy

`user="jane@company.com"` on the client, the conversation, or a single call.
It sets the OpenAI `user` field, and it is what keeps your end users apart:
each value is one person, with their own thread and memory, and a call without
it lands in the one thread shared by everyone on the key. The part before an
`@` becomes the name the agent sees. Values are scoped to the key, so a new or
rotated key starts every person over.

## Verifying a deployment

`magnus-livecheck` runs the fourteen checks in [CONTRACT.md](CONTRACT.md)
against a real deployment and exits non-zero unless all of them pass:

```bash
export MAGNUS_BASE_URL=https://app.iamagnus.com
export MAGNUS_API_KEY=magnus_sys_...   # a key created for a test agent

magnus-livecheck
```

Checks 5, 8, 9, 10 and 13 run real turns, which cost tokens and are recorded
like any other conversation. A key answers only as its own agent, so create the
key for a test agent.

## API

| | |
|---|---|
| `MagnusClient(base_url, api_key, *, user, timeout, max_retries, auth_scheme, session)` | |
| `health()` | unauthenticated reachability probe |
| `list_agents()` / `get_agent(id)` | agents; an unknown id is `None` |
| `chat(agent, messages, *, session_id, idempotency_key, user, extra_body)` | one buffered turn |
| `stream_chat(agent, messages, *, session_id, user, include_usage)` | one streamed turn |
| `send_message(agent, content, ...)` | text in, text out |
| `conversation(agent, *, user, session_id)` | a thread for one end user: `.send()`, `.stream()`, `.reset()` |

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

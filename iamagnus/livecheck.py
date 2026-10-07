"""End-to-end check: run the CONTRACT.md checklist against a real Magnus.

    MAGNUS_BASE_URL=https://... MAGNUS_API_KEY=magnus_... magnus-livecheck

Exits 0 only when every check passes. Checks 5, 8, 9, 10 and 13 run real turns
against the target agent — they cost tokens and are recorded like any
other conversation.
"""
import argparse
import os
import sys
import time
import uuid
from typing import Callable, List, Optional, Tuple

from . import __version__
from .client import MagnusClient
from .errors import MagnusError, UnsupportedParameterError

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Check:
    def __init__(self, number: int, name: str, run: Callable[[], Optional[str]]):
        self.number = number
        self.name = name
        self.run = run


def build_checks(client: MagnusClient, agent_id: Optional[str], prompt: str) -> Tuple[List[Check], dict]:
    """The fifteen checks, sharing a scratch dict so later ones reuse earlier results."""
    state: dict = {"agent_id": agent_id}

    def c1_health():
        body = client.health()
        if body.get("status") != "ok":
            return f"health said {body.get('status')!r}, expected 'ok'"
        return None

    def c2_list():
        agents = client.list_agents()
        if not agents:
            return "no agents visible to this key (wrong org, or none configured)"
        state.setdefault("agents", agents)
        if not state["agent_id"]:
            state["agent_id"] = agents[0]["id"]
        elif state["agent_id"] not in [a["id"] for a in agents]:
            return (f"agent {state['agent_id']!r} is not in this key's list: "
                    f"{[a['id'] for a in agents]}")
        return None

    def c3_get():
        agent = client.get_agent(state["agent_id"])
        if not agent or agent.get("id") != state["agent_id"]:
            return f"GET /v1/models/{state['agent_id']} did not return that agent"
        return None

    def c4_missing():
        if client.get_agent(f"no_such_agent_{uuid.uuid4().hex[:8]}") is not None:
            return "an invented model id came back as if it existed"
        return None

    def c5_turn():
        response = client.chat(state["agent_id"], [{"role": "user", "content": prompt}])
        state["response"] = response
        text = (response.get("choices") or [{}])[0].get("message", {}).get("content")
        if not text:
            return "the turn returned no text"
        state["text"] = text
        return None

    def c6_extensions():
        magnus = state["response"].get("magnus") or {}
        if not magnus.get("session_id"):
            return "response carried no magnus.session_id"
        if not magnus.get("session_source"):
            return "response carried no magnus.session_source"
        return None

    def c7_usage():
        usage = state["response"].get("usage") or {}
        if not usage.get("total_tokens"):
            return f"usage.total_tokens was {usage.get('total_tokens')!r}"
        source = (state["response"].get("magnus") or {}).get("usage_source")
        if source not in ("measured", "estimated"):
            return f"magnus.usage_source was {source!r}"
        state["usage_source"] = source
        return None

    def c8_continuity():
        chat = client.conversation(state["agent_id"])
        chat.send(prompt)
        first = chat.session_id
        if not first:
            return "the first turn produced no session id"
        chat.send("¿Y algo más?")
        if not chat.session_id:
            return "the session was lost on the second turn"
        state["continuity"] = f"{first[:8]}… → {chat.session_id[:8]}…"
        return None

    def c9_stream():
        stream = client.stream_chat(state["agent_id"], [{"role": "user", "content": prompt}])
        pieces = list(stream)
        if not stream.text:
            return "the streamed turn produced no text"
        state["stream_shape"] = "token by token" if len(pieces) > 1 else "single delta"
        if not stream.session_id:
            return "the stream carried no session id"
        return None

    def c10_stream_usage():
        stream = client.stream_chat(
            state["agent_id"], [{"role": "user", "content": prompt}], include_usage=True,
        )
        list(stream)
        if not stream.usage or not stream.usage.get("total_tokens"):
            return f"include_usage produced {stream.usage!r}"
        return None

    def c11_refused():
        # Sent via extra_body: this check is about the *server's* refusal, and
        # the typed surface deliberately has no way to pass `tools`.
        try:
            client.chat(
                state["agent_id"],
                [{"role": "user", "content": prompt}],
                extra_body={"tools": [{
                    "type": "function",
                    "function": {"name": "f", "parameters": {}},
                }]},
            )
        except UnsupportedParameterError as exc:
            if exc.param != "tools":
                return f"refused, but named param={exc.param!r} instead of 'tools'"
            return None
        except MagnusError as exc:
            return f"expected an unsupported_parameter error, got {exc}"
        return "the server accepted 'tools', which it is documented to refuse"

    def c12_bad_session():
        try:
            client.chat(state["agent_id"], [{"role": "user", "content": prompt}],
                        session_id="not-a-uuid")
        except ValueError:
            return None
        return "a non-UUID session_id was not refused"

    def c13_idempotency():
        key = f"livecheck-{uuid.uuid4()}"
        messages = [{"role": "user", "content": prompt}]
        first = client.chat(state["agent_id"], messages, idempotency_key=key)
        second = client.chat(state["agent_id"], messages, idempotency_key=key)
        if first.get("id") != second.get("id"):
            return (f"the same Idempotency-Key ran two turns "
                    f"({first.get('id')} vs {second.get('id')})")
        return None

    def c14_budget():
        if client.rate_limit_remaining is None:
            return "no X-RateLimit-Remaining header was seen on any response"
        state["budget"] = client.rate_limit_remaining
        return None

    def c15_updates():
        # A server older than the endpoint answers 404, and the SDK's
        # updates()/follow() would fail for every user of this release.
        page = client.conversation_updates(state["agent_id"], user=f"livecheck-{uuid.uuid4()}")
        if not isinstance(page.get("data"), list) or not isinstance(page.get("handoff"), bool):
            return "GET /v1/conversations/updates did not answer a page with `data` and `handoff`"
        return None

    checks = [
        Check(1, "health probe answers", c1_health),
        Check(2, "the key can list agents", c2_list),
        Check(3, "a single agent can be retrieved", c3_get),
        Check(4, "an unknown agent is absent, not an error", c4_missing),
        Check(5, "a buffered turn returns text", c5_turn),
        Check(6, "the magnus extensions survive", c6_extensions),
        Check(7, "usage is reported with its provenance", c7_usage),
        Check(8, "a conversation keeps its session", c8_continuity),
        Check(9, "a streamed turn parses and closes", c9_stream),
        Check(10, "include_usage reports usage", c10_stream_usage),
        Check(11, "'tools' is refused by name", c11_refused),
        Check(12, "a non-UUID session_id is refused locally", c12_bad_session),
        Check(13, "one idempotency key runs one turn", c13_idempotency),
        Check(14, "the rate-limit budget is observable", c14_budget),
        Check(15, "the team's replies can be fetched", c15_updates),
    ]
    return checks, state


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="magnus-livecheck",
        description="Run the CONTRACT.md checklist against a real Magnus deployment.",
    )
    parser.add_argument("--base-url", default=os.environ.get("MAGNUS_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("MAGNUS_API_KEY"))
    parser.add_argument("--agent", default=os.environ.get("MAGNUS_AGENT"),
                        help="agent id to test against (default: the first one listed)")
    parser.add_argument("--prompt", default="Hi, what can you do?")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    if not args.base_url or not args.api_key:
        parser.error(
            "MAGNUS_BASE_URL and MAGNUS_API_KEY are required "
            "(or pass --base-url / --api-key)."
        )

    paint = (lambda text, _color: text) if args.no_color else (
        lambda text, color: f"{color}{text}{RESET}"
    )

    print(f"iamagnus {__version__} → {args.base_url}")
    print(paint("checks 5, 8, 9, 10 and 13 run real turns and cost tokens.\n", YELLOW))

    client = MagnusClient(args.base_url, args.api_key, timeout=args.timeout, max_retries=1)
    checks, state = build_checks(client, args.agent, args.prompt)

    failures = []
    with client:
        for check in checks:
            started = time.time()
            try:
                problem = check.run()
            except MagnusError as exc:
                problem = str(exc)
            except Exception as exc:  # a bug in the SDK is also a failed check
                problem = f"{type(exc).__name__}: {exc}"
            elapsed = f"{(time.time() - started) * 1000:.0f}ms"

            if problem is None:
                mark = paint("PASS", GREEN)
            else:
                mark = paint("FAIL", RED)
                failures.append((check, problem))
            print(f"  {mark} {check.number:>2}. {check.name} {paint(elapsed, DIM)}")
            if problem:
                print(f"        {paint(problem, RED)}")

    print()
    if state.get("agent_id"):
        print(f"  agent          {state['agent_id']}")
    if state.get("stream_shape"):
        print(f"  stream shape   {state['stream_shape']}")
    if state.get("usage_source"):
        note = "" if state["usage_source"] == "measured" else "  (character heuristic — do not bill on this)"
        print(f"  usage source   {state['usage_source']}{note}")
    if state.get("continuity"):
        print(f"  session        {state['continuity']}")
    if state.get("budget") is not None:
        print(f"  budget left    {state['budget']}")

    print()
    if failures:
        print(paint(f"{len(failures)} of {len(checks)} checks failed — this deployment does not meet the contract.", RED))
        return 1
    print(paint(f"all {len(checks)} checks passed — this deployment meets the contract.", GREEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Writes are put to the human by the server, in every MCP client; reads never are.

Fake sessions stand in for the client: one that supports elicitation (and answers
accept/decline/cancel), and one that does not.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from fsr_agentic_toolkit.verbs import approval as A
from fsr_agentic_toolkit.verbs import mcp as M


class Session:
    def __init__(self, *, can_ask=True, action="accept", approve=True, fail=False):
        self.can_ask, self.action, self.approve, self.fail = can_ask, action, approve, fail
        self.asked, self.related = [], []

    def check_client_capability(self, _cap):
        return self.can_ask

    async def elicit_form(self, message, requested_schema, related_request_id=None):
        self.asked.append(message)
        self.related.append(related_request_id)
        if self.fail:
            raise RuntimeError("no back-channel")
        return SimpleNamespace(action=self.action, content={"approve": self.approve})


def _ask(session, name, args=None, mode="auto", request_id=7):
    return asyncio.run(A.require_approval(session, name, args or {}, "159 (https://box)", mode=mode,
                                          request_id=request_id))


def test_a_read_is_never_asked_about():
    s = Session()
    assert _ask(s, "list_modules") is None and not s.asked


def test_a_write_is_put_to_the_user_and_proceeds_on_approval():
    s = Session()
    assert _ask(s, "create_record", {"module": "alerts", "data": {"name": "x"}}) is None
    assert len(s.asked) == 1 and "159 (https://box)" in s.asked[0] and "create_record" in s.asked[0]


def test_what_a_person_needs_is_in_the_first_lines_a_client_will_show():
    """Claude Code collapses a long prompt after ~3 lines, so tool, target, a one-line
    summary of the arguments and the instruction must all come before the detail."""
    s = Session()
    _ask(s, "create_record", {"module": "alerts", "data": {"name": "Approval test", "severity": "Low"}})
    head = s.asked[0].splitlines()[:3]
    assert "create_record" in head[0] and "159 (https://box)" in head[0]
    assert '"Approval test"' in head[1] and '"severity": "Low"' in head[1]
    assert "Yes" in head[2] and "approve" in head[2]
    assert s.asked[0].count("\n\n") == 1  # no blank lines inside the part that stays visible


@pytest.mark.parametrize(("action", "approve"), [("decline", True), ("cancel", True), ("accept", False)])
def test_anything_but_an_explicit_yes_refuses_and_says_not_to_retry(action, approve):
    out = _ask(Session(action=action, approve=approve), "create_record", {"module": "alerts"})
    assert out["error"]["type"] == "ApprovalDeclined" and "Do not retry" in out["error"]["message"]


def test_credentials_in_the_arguments_are_masked_in_the_prompt():
    s = Session()
    _ask(s, "create_connector_configuration",
         {"connector": "virustotal", "config": {"server": "x", "api_key": "LEAK-123", "password": "LEAK-456"}})
    assert "LEAK-123" not in s.asked[0] and "LEAK-456" not in s.asked[0]
    assert "virustotal" in s.asked[0] and '"server": "x"' in s.asked[0]


def test_a_call_the_policy_would_refuse_is_refused_without_asking(monkeypatch):
    s = Session()
    out = _ask(s, "delete_record", {"module": "alerts", "uuid": "u"})  # destructive, no confirm
    assert out["error"]["type"] == "confirmation_required" and not s.asked
    monkeypatch.setenv("FSR_MCP_READONLY", "1")
    out = _ask(s, "create_record", {"module": "alerts"})
    assert out["error"]["type"] == "ToolDisabled" and not s.asked


def test_a_client_that_cannot_be_asked_runs_in_auto_and_is_refused_in_require():
    assert _ask(Session(can_ask=False), "create_record", mode="auto") is None
    out = _ask(Session(can_ask=False), "create_record", mode="require")
    assert out["error"]["type"] == "ApprovalUnavailable"


def test_the_default_posture_refuses_a_write_a_client_cannot_be_asked_to_approve(monkeypatch):
    """With FSR_MCP_APPROVAL unset the mode is require, so a write to a client that cannot be
    elicited is refused rather than silently run -- a write reaches the box only after a human
    approves it (or the client is set to auto-accept the prompt)."""
    monkeypatch.delenv("FSR_MCP_APPROVAL", raising=False)
    out = asyncio.run(A.require_approval(Session(can_ask=False), "create_record",
                                         {"module": "alerts"}, "159 (https://box)"))
    assert out["error"]["type"] == "ApprovalUnavailable"


def test_off_never_asks():
    s = Session()
    assert _ask(s, "create_record", mode="off") is None and not s.asked


def test_mode_comes_from_the_environment_and_defaults_to_require():
    assert A.approval_mode({}) == "require"
    assert A.approval_mode({"FSR_MCP_APPROVAL": "AUTO"}) == "auto"
    assert A.approval_mode({"FSR_MCP_APPROVAL": "REQUIRE"}) == "require"
    assert A.approval_mode({"FSR_MCP_APPROVAL": "nonsense"}) == "require"


def test_destructive_prompts_say_so():
    s = Session()
    _ask(s, "delete_record", {"module": "alerts", "uuid": "u", "confirm": True})
    assert "IRREVERSIBLE" in s.asked[0].splitlines()[0]
    assert "confirm" not in s.asked[0].replace("confirmation", "")  # the gate argument is not shown


def test_every_advertised_tool_carries_hints_that_match_its_tier():
    from mcp import types  # noqa: F401  (import check: SDK present)

    reg = SimpleNamespace(names=lambda: ["a"], default="a",
                          describe=lambda: [{"instance": "a", "base_url": "https://box", "default": True}])
    tools = {t.name: t for t in M._mcp_tools(reg)}
    assert tools["list_modules"].annotations.read_only_hint is True
    assert tools["create_record"].annotations.read_only_hint is False
    assert tools["create_record"].annotations.destructive_hint is False
    assert tools["delete_record"].annotations.destructive_hint is True
    assert tools["run_playbook_yaml"].annotations.read_only_hint is False
    assert tools["setup_connector"].annotations.read_only_hint is False
    assert tools["survey_box"].annotations.read_only_hint is True
    assert tools[M.LIST_INSTANCES].annotations.read_only_hint is True
    assert all(t.annotations is not None for t in tools.values())


def _roundtrip(monkeypatch, answer):
    """A real MCP client and the real server over in-memory streams. ``answer`` is what the
    client's elicitation callback replies; the box is a recording fake."""
    import anyio
    from mcp import ClientSession, types
    from mcp.shared.memory import create_client_server_memory_streams

    reached = []
    monkeypatch.setattr(M, "_call", lambda client, name, args: reached.append((name, args))
                        or M._text_content({"ok": True, "ran": name}))
    server = M.build_server(SimpleNamespace(base_url="https://box"))  # one bare client
    prompts = []

    async def elicit(_ctx, params):
        prompts.append(params.message)
        return types.ElicitResult(action=answer[0], content={"approve": answer[1]})

    out = {}

    async def run():
        async with (create_client_server_memory_streams() as (client_streams, server_streams),
                    anyio.create_task_group() as tg):
            tg.start_soon(lambda: server.run(*server_streams, server.create_initialization_options()))
            async with ClientSession(*client_streams, elicitation_callback=elicit) as client:
                await client.initialize()
                listed = {t.name: t for t in (await client.list_tools()).tools}
                read = await client.call_tool("list_modules", {})
                write = await client.call_tool("create_record", {"module": "alerts", "data": {"name": "x"}})
                out.update(listed=listed, read=read, write=write)
                tg.cancel_scope.cancel()

    anyio.run(run)
    return out["listed"], out["read"], out["write"], reached, prompts


def test_end_to_end_an_approved_write_runs_and_a_read_never_prompts(monkeypatch):
    listed, _read, write, reached, prompts = _roundtrip(monkeypatch, ("accept", True))
    assert listed["list_modules"].annotations.read_only_hint is True
    assert listed["create_record"].annotations.read_only_hint is False
    assert [r[0] for r in reached] == ["list_modules", "create_record"]
    assert len(prompts) == 1 and "create_record" in prompts[0] and "default (https://box)" in prompts[0]
    assert json.loads(write.content[0].text)["ran"] == "create_record"


def test_end_to_end_a_declined_write_never_reaches_the_box(monkeypatch):
    _, _, write, reached, prompts = _roundtrip(monkeypatch, ("decline", False))
    assert [r[0] for r in reached] == ["list_modules"]  # only the read ran
    assert len(prompts) == 1
    assert json.loads(write.content[0].text)["error"]["type"] == "ApprovalDeclined"


def test_claude_rules_allow_every_read_and_ask_for_every_write():
    from fsr_agentic_toolkit import init_cmd
    from fsr_agentic_toolkit.verbs.tools import enabled_tools

    perms = init_cmd.claude_permissions("fortisoar")["permissions"]
    for t in enabled_tools({}):
        rule = f"mcp__fortisoar__{t.name}"
        assert (rule in perms["ask"]) == t.mutating
        assert (rule in perms["allow"]) == (not t.mutating)
    assert not set(perms["allow"]) & set(perms["ask"])
    assert "mcp__fortisoar__setup_connector" in perms["ask"]
    assert "mcp__fortisoar__run_playbook_yaml" in perms["ask"]


def test_merging_rules_keeps_what_the_user_already_had(tmp_path):
    from fsr_agentic_toolkit import init_cmd

    new = init_cmd.claude_permissions("fortisoar")
    mine = {"model": "x", "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm *)"]}}
    merged = init_cmd.merge_permissions(mine, new)
    assert merged["model"] == "x" and merged["permissions"]["deny"] == ["Bash(rm *)"]
    assert "Bash(ls)" in merged["permissions"]["allow"]
    assert init_cmd.merge_permissions(merged, new) == merged  # idempotent


def test_the_approval_is_tied_to_the_tool_call_that_triggered_it():
    """Over stdio a server-initiated request is only accepted when it names the in-flight
    request; without related_request_id the client side raises NoBackChannelError."""
    s = Session()
    _ask(s, "create_record", {"module": "alerts"}, request_id=42)
    assert s.related == [42]


def test_a_failed_prompt_refuses_the_write_in_every_mode():
    for mode in ("auto", "require"):
        out = _ask(Session(fail=True), "create_record", {"module": "alerts"}, mode=mode)
        assert out["error"]["type"] == "ApprovalUnavailable" and "Nothing was changed" in out["error"]["message"]


def test_a_client_that_never_answers_becomes_a_refusal_not_a_hang(monkeypatch):
    monkeypatch.setenv("FSR_MCP_APPROVAL_TIMEOUT", "1")

    class Silent(Session):
        async def elicit_form(self, message, requested_schema, related_request_id=None):
            await asyncio.sleep(30)

    out = _ask(Silent(), "create_record", {"module": "alerts"})
    assert out["error"]["type"] == "ApprovalUnavailable" and "nobody answered" in out["error"]["message"]


def test_the_modern_era_asks_with_an_input_required_result_and_honours_only_the_matching_retry():
    from mcp_types import ElicitResult, InputRequiredResult

    class NoChannel(Session):
        async def elicit_form(self, message, requested_schema, related_request_id=None):
            class NoBackChannelError(Exception):
                pass
            raise NoBackChannelError("no back-channel")

    args = {"module": "alerts", "data": {"name": "x"}}
    first = _ask(NoChannel(), "create_record", args)
    assert isinstance(first, InputRequiredResult) and "approve" in first.input_requests
    state = first.request_state

    def retry(answer, call_args=args, st=state):
        return asyncio.run(A.require_approval(
            NoChannel(), "create_record", call_args, "box", mode="auto",
            input_responses={"approve": answer}, request_state=st))

    yes = ElicitResult(action="accept", content={"approve": True})
    assert retry(yes) is None  # approved, same call
    assert retry(ElicitResult(action="decline"))["error"]["type"] == "ApprovalDeclined"
    # an approval for one call cannot be spent on another
    other = {"module": "alerts", "data": {"name": "DIFFERENT"}}
    assert retry(yes, call_args=other)["error"]["type"] == "ApprovalMismatch"
    assert retry(yes, st=None)["error"]["type"] == "ApprovalMismatch"


def _stdio_call(answer):
    """The real server in a subprocess over stdio, a real MCP client, one write. This is the
    transport the in-memory tests cannot cover: on stdio a modern client refuses a
    server-initiated request, so approval has to ride on the call itself."""
    import sys
    from pathlib import Path

    import anyio
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    asked = []

    async def elicit(_ctx, params):
        asked.append(params.message)
        return types.ElicitResult(action=answer[0], content={"approve": answer[1]})

    out = {}

    async def run():
        params = StdioServerParameters(command=sys.executable,
                                       args=[str(Path(__file__).with_name("stub_mcp_server.py"))])
        async with stdio_client(params) as (r, w), ClientSession(r, w, elicitation_callback=elicit) as client:
            await client.initialize()
            out["write"] = await client.call_tool("create_record", {"module": "alerts", "data": {"name": "x"}})
            out["read"] = await client.call_tool("list_modules", {})

    anyio.run(run)
    return out, asked


def test_over_real_stdio_a_declined_write_is_refused_and_an_approved_one_reaches_the_box():
    declined, asked = _stdio_call(("decline", False))
    assert len(asked) == 1 and "create_record" in asked[0]
    assert "ApprovalDeclined" in declined["write"].content[0].text
    approved, asked = _stdio_call(("accept", True))
    assert len(asked) == 1
    assert "stub box" in approved["write"].content[0].text  # it got past the gate and hit the box
    assert "ApprovalDeclined" not in approved["write"].content[0].text


def test_init_prints_a_codex_snippet_that_is_valid_toml_with_the_env_block():
    import argparse
    import tomllib

    from fsr_agentic_toolkit import init_cmd

    args = argparse.Namespace(url="fortisoar.example.com", api_key="<your-api-key>", username=None,
                              password=None, no_verify_ssl=True, readonly=False)
    text = init_cmd._codex_snippet(args)
    doc = tomllib.loads("\n".join(l for l in text.splitlines() if not l.startswith("#")))
    server = doc["mcp_servers"]["fortisoar"]
    assert server["env"]["FSR_BASE_URL"] == "fortisoar.example.com"
    assert server["env"]["FSR_API_KEY"] == "<your-api-key>" and server["command"]

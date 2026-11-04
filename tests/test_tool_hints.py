"""The server leaves approval to the MCP client and tells it what each tool does.

Every tool carries accurate ``readOnlyHint`` / ``destructiveHint`` annotations (the client
decides what to ask about from those), the server never prompts on its own, and the Claude Code
rules generated from the registry allow every read and ask for every write.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fsr_agentic_toolkit.verbs import mcp as M


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


def test_end_to_end_the_server_never_prompts_even_for_a_client_that_could_be_asked(monkeypatch):
    """A real MCP client that supports elicitation and a real server over in-memory streams:
    the write runs with no prompt from the server, and the client still sees the hints it uses
    to decide what to ask the user."""
    import anyio
    from mcp import ClientSession, types
    from mcp.shared.memory import create_client_server_memory_streams

    reached, prompts = [], []
    monkeypatch.setattr(M, "_call", lambda client, name, args: reached.append(name)
                        or M._text_content({"ok": True, "ran": name}))
    server = M.build_server(SimpleNamespace(base_url="https://box"))

    async def elicit(_ctx, params):  # would be the user's prompt; the server must never trigger it
        prompts.append(params.message)
        return types.ElicitResult(action="decline")

    out = {}

    async def run():
        async with (create_client_server_memory_streams() as (client_streams, server_streams),
                    anyio.create_task_group() as tg):
            tg.start_soon(lambda: server.run(*server_streams, server.create_initialization_options()))
            async with ClientSession(*client_streams, elicitation_callback=elicit) as client:
                await client.initialize()
                out["tools"] = {t.name: t for t in (await client.list_tools()).tools}
                out["write"] = await client.call_tool(
                    "create_record", {"module": "alerts", "data": {"name": "x"}})
                tg.cancel_scope.cancel()

    anyio.run(run)
    assert prompts == []
    assert reached == ["create_record"]
    assert json.loads(out["write"].content[0].text)["ran"] == "create_record"
    assert out["tools"]["create_record"].annotations.read_only_hint is False
    assert out["tools"]["delete_record"].annotations.destructive_hint is True
    assert out["tools"]["list_modules"].annotations.read_only_hint is True

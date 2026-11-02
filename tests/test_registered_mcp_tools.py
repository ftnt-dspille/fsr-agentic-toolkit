"""list_registered_mcp_tools / call_registered_mcp_tool hand the server name and
arguments straight to pyfsr's registered-server MCP client. The call counts as a
write (the remote tool decides its own side effects); the listing does not."""
from __future__ import annotations

from types import SimpleNamespace

from fsr_agentic_toolkit.verbs import tools


class _AI:
    def __init__(self):
        self.calls = []

    def list_registered_tools(self, server, token=None):
        self.calls.append(("list", server, token))
        return [SimpleNamespace(name="ask_question", description="Ask a repo question")]

    def call_registered_tool(self, server, tool, arguments, token=None):
        self.calls.append(("call", server, tool, arguments, token))
        return SimpleNamespace(ok=False, status=None, result="answer", error=None)


def test_list_passes_server_and_shapes_tools():
    client = SimpleNamespace(ai=_AI())
    out = tools.REGISTRY["list_registered_mcp_tools"].handler(client, server="agentlab-deepwiki")
    assert out == {"server": "agentlab-deepwiki", "tools": [{"name": "ask_question", "description": "Ask a repo question"}]}
    assert client.ai.calls == [("list", "agentlab-deepwiki", None)]


def test_call_defaults_arguments_and_returns_result():
    client = SimpleNamespace(ai=_AI())
    out = tools.REGISTRY["call_registered_mcp_tool"].handler(client, server="s", tool="ask_question")
    assert (out["ok"], out["result"], out["error"]) == (True, "answer", None)
    assert client.ai.calls == [("call", "s", "ask_question", {}, None)]


def test_call_is_a_write_and_list_is_not():
    assert tools.REGISTRY["call_registered_mcp_tool"].mutating is True
    assert tools.REGISTRY["call_registered_mcp_tool"].destructive is False
    assert tools.REGISTRY["list_registered_mcp_tools"].mutating is False


def test_call_ok_follows_the_fortisoar_envelope_when_present():
    class _Failing(_AI):
        def call_registered_tool(self, server, tool, arguments, token=None):
            return SimpleNamespace(ok=False, status="failed", result=None, error="boom")

    out = tools.REGISTRY["call_registered_mcp_tool"].handler(SimpleNamespace(ai=_Failing()), server="s", tool="t")
    assert (out["ok"], out["error"]) == (False, "boom")

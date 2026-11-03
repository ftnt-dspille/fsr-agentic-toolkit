"""The Codex snippet must carry a startup timeout: with Codex's 10s default a
first server start can lose the race and the chat runs with no FortiSOAR tools."""
import argparse
import tomllib

from fsr_agentic_toolkit import init_cmd


def _args(**kw):
    base = dict(url="https://fortisoar.example.com", username="agent", password="<your-password>",
                api_key=None, no_verify_ssl=True, readonly=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_codex_snippet_is_valid_toml_with_startup_timeout():
    text = init_cmd._codex_snippet(_args())
    cfg = tomllib.loads("\n".join(ln for ln in text.splitlines() if not ln.startswith("#")))
    srv = cfg["mcp_servers"]["fortisoar"]
    assert srv["startup_timeout_sec"] == 60
    assert srv["env"]["FSR_MCP_READONLY"] == "1"


def test_uvx_fallback_is_pinned(monkeypatch):
    monkeypatch.setattr(init_cmd.shutil, "which", lambda _name: None)
    monkeypatch.setattr(init_cmd.sys, "executable", "/nonexistent/python")
    command, args = init_cmd._server_command()
    assert command == "uvx"
    assert args[1].startswith("fsr-agentic-toolkit==") or args[1] == "fsr-agentic-toolkit"

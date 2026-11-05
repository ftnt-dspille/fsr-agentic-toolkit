"""dispatch() bounds every tool call with a wall-clock deadline."""

import threading

import pytest

from fsr_agentic_toolkit.verbs import tools


def test_default_deadline_is_bounded(monkeypatch):
    monkeypatch.delenv("FSR_MCP_TOOL_TIMEOUT", raising=False)
    assert tools._tool_deadline({}) == tools.DEFAULT_TOOL_TIMEOUT


def test_env_overrides_and_zero_disables(monkeypatch):
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "7")
    assert tools._tool_deadline({}) == 7
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "0")
    assert tools._tool_deadline({}) is None
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "garbage")
    assert tools._tool_deadline({}) == tools.DEFAULT_TOOL_TIMEOUT


def test_tool_own_timeout_extends_deadline(monkeypatch):
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "10")
    assert tools._tool_deadline({"timeout": 600}) == 600 + tools._TIMEOUT_MARGIN
    # a short own timeout never lowers the ceiling below the default
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "100")
    assert tools._tool_deadline({"timeout": 1}) == 100
    assert tools._tool_deadline({"timeout": True}) == 100


def test_hung_call_times_out_and_reports_it():
    gate = threading.Event()
    with pytest.raises(tools.ToolTimeout) as exc:
        tools._call_with_deadline(lambda: gate.wait(30), 0.2, tool="slow_tool")
    gate.set()
    assert "slow_tool" in str(exc.value) and "read the state back" in str(exc.value)


def test_fast_call_and_errors_pass_through():
    assert tools._call_with_deadline(lambda: 41 + 1, 5, tool="t") == 42
    assert tools._call_with_deadline(lambda: "x", None, tool="t") == "x"
    with pytest.raises(ValueError, match="boom"):
        tools._call_with_deadline(lambda: (_ for _ in ()).throw(ValueError("boom")), 5, tool="t")


def test_dispatch_returns_structured_timeout(monkeypatch):
    monkeypatch.setenv("FSR_MCP_TOOL_TIMEOUT", "0.2")
    gate = threading.Event()
    spec = tools.ToolSpec("zz_hang", "d", {"type": "object", "properties": {}}, lambda c, **kw: gate.wait(30))
    monkeypatch.setitem(tools.REGISTRY, "zz_hang", spec)
    out = tools.dispatch(None, "zz_hang", {})
    gate.set()
    assert out["error"]["type"] == "ToolTimeout"
    assert out["error"]["tool"] == "zz_hang"

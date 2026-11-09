"""The MCP server over streamable HTTP: token gate, 401, and a tools/list round trip on loopback."""
from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from fsr_agentic_toolkit.verbs import mcp_http


class FakeClient:
    def list_modules(self, refresh=False):
        return [{"type": "alerts", "label": "Alerts", "plural": "alerts"}]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    import uvicorn

    port = _free_port()
    app = mcp_http.build_app(FakeClient(), "s3cret")
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}{mcp_http.MCP_PATH}"
    srv.should_exit = True
    t.join(timeout=10)


def test_refuses_public_host_without_token(monkeypatch):
    monkeypatch.delenv(mcp_http.TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit, match="without a token"):
        mcp_http.serve_http("0.0.0.0", 1, target=FakeClient())


def test_token_from_file_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv(mcp_http.TOKEN_ENV, "from-env")
    f = tmp_path / "tok"
    f.write_text("from-file\n")
    assert mcp_http.load_token(str(f)) == "from-file"
    assert mcp_http.load_token(None) == "from-env"
    monkeypatch.setenv(mcp_http.TOKEN_ENV, "  ")
    assert mcp_http.load_token(None) is None


def test_loopback_detection():
    assert mcp_http.is_loopback("127.0.0.1") and mcp_http.is_loopback("localhost") and mcp_http.is_loopback("::1")
    assert not mcp_http.is_loopback("0.0.0.0") and not mcp_http.is_loopback("192.0.2.10")


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "s3cret"])
def test_bad_or_missing_token_is_401(server, auth):
    req = urllib.request.Request(server, data=b"{}", method="POST",
                                 headers={"content-type": "application/json", **({"authorization": auth} if auth else {})})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 401


def test_tools_list_round_trip(server):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async def go():
        async with create_mcp_http_client(headers={"Authorization": "Bearer s3cret"}) as http, \
                streamable_http_client(server, http_client=http) as streams, \
                ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            listed = await session.list_tools()
            return [t.name for t in listed.tools]

    names = asyncio.run(go())
    assert "list_modules" in names or len(names) > 10

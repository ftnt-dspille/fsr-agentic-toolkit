"""The real toolkit MCP server over stdio on a stub box, for tests that need a real subprocess."""
import asyncio
from types import SimpleNamespace

from fsr_agentic_toolkit.verbs import mcp as M


class Box(SimpleNamespace):
    def __getattr__(self, name):
        raise RuntimeError(f"stub box: {name} reached the box")


asyncio.run(M.serve(client=Box(base_url="https://stub.invalid")))

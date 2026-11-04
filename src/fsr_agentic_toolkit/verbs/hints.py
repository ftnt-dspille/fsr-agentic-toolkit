"""Tool annotations: how an MCP client learns which tools only read and which change things.

Approval is the MCP client's job. Every coding agent already asks the user before a tool runs,
and it decides what to ask about from the standard annotations a server advertises:
``readOnlyHint`` and ``destructiveHint``. This server therefore adds no prompt of its own; it
only makes sure those annotations are accurate, derived from each tool's tier in the registry.
"""
from __future__ import annotations

from typing import Any

from .tools import REGISTRY


def tool_hints(name: str) -> dict[str, Any]:
    """``ToolAnnotations`` fields for a tool: reads are read-only, writes are not, the
    irreversible ones are marked destructive. Every tool talks to an external system."""
    spec = REGISTRY.get(name)
    if spec is None:  # the list_instances meta-tool and anything unregistered
        return {"read_only_hint": True, "open_world_hint": False}
    if not spec.mutating:
        return {"read_only_hint": True, "open_world_hint": True}
    return {"read_only_hint": False, "destructive_hint": bool(spec.destructive),
            "idempotent_hint": False, "open_world_hint": True}

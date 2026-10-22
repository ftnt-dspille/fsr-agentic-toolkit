"""The one tool spec type: SDK verbs and workflows are both ToolSpecs, so one
registry, one gate and one dispatch serve every surface (MCP, CLI, SDK)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """A single agent-callable FortiSOAR operation.

    ``input_schema`` is a JSON Schema (draft 2020-12 compatible object schema)
    describing the tool's arguments; ``handler`` runs the operation against a
    live client and returns a JSON-serializable result.

    ``mutating`` marks a tool that writes to the appliance (create/update/run/
    schedule); ``destructive`` marks the subset that removes state, irreversibly
    commits it, or raises privilege (delete/publish, plus install_connector --
    it ships executable code -- and create_user -- it grants roles). The safety
    gate in :func:`dispatch` reads these: ``FSR_MCP_READONLY=1`` refuses every
    mutating tool, and a destructive tool always requires ``confirm: true`` in
    its arguments. A destructive tool is always also mutating (enforced in tests).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    mutating: bool = False
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Generic ``{name, description, input_schema}`` (no handler).

        A destructive tool's schema is rendered with the ``confirm`` argument
        the dispatch gate requires, so agents discover it instead of hitting a
        confirmation_required error first.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.rendered_schema(),
        }

    @property
    def tier(self) -> int:
        """fsr_playbooks' tier for this tool: 1 a read, 2 a write, 3 a write that
        needs a human yes. The one gate reads it: read-only mode refuses tier >= 2,
        and tier 3 needs ``confirm: true`` here (an approval card on-box)."""
        return 3 if self.destructive else 2 if self.mutating else 1

    def rendered_schema(self) -> dict[str, Any]:
        """The input schema as an agent should see it (``confirm`` injected)."""
        if not self.destructive:
            return self.input_schema
        schema = dict(self.input_schema)
        props = dict(schema.get("properties") or {})
        props["confirm"] = {
            "type": "boolean",
            "description": "This operation is high-impact (destructive or privileged) and never "
            "runs without confirm=true. Set it only after telling the user exactly what will change.",
        }
        schema["properties"] = props
        return schema


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema

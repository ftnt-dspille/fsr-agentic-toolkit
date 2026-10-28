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

    ``workflow`` marks a higher-level, evidence-backed verb that does a whole job
    end to end (provision_module, orient, debug_run, ...). ``advanced`` marks a
    low-level building block that a workflow usually subsumes (create_module,
    publish, create_record, ...): still available, but an agent should reach for
    the workflow first. These drive how the surface is presented -- workflows are
    advertised first and advanced tools last, and each carries a one-word lead tag
    in its advertised description -- so a model picks the recommended path instead
    of hand-rolling it from primitives. A tool is never both.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    mutating: bool = False
    destructive: bool = False
    workflow: bool = False
    advanced: bool = False
    #: Former names kept as hidden dispatch aliases after a rename. They resolve
    #: to this spec through the registry and the CLI, so existing scripts, saved
    #: MCP calls and skills keep working, but they are never advertised (only the
    #: current :attr:`name` appears in the tool catalog).
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.workflow and self.advanced:
            raise ValueError(f"{self.name}: a tool is either a workflow or advanced, not both")

    @property
    def surface_rank(self) -> int:
        """Advertise order: workflows first (0), ordinary tools next (1), advanced last (2).

        A model browsing or searching the deferred tool catalog meets the
        recommended higher-level verbs before the low-level primitives they
        subsume, so it takes the workflow instead of decomposing the task."""
        return 0 if self.workflow else 2 if self.advanced else 1

    @property
    def advertised_description(self) -> str:
        """Description as advertised, with a one-word lead tag for workflow/advanced.

        The tag survives even a terse catalog row (name + first words), which is
        often all a model reads before selecting a tool, so the hierarchy is legible
        without the model having to open the full description."""
        if self.workflow:
            return f"WORKFLOW (preferred path) -- {self.description}"
        if self.advanced:
            return f"[low-level building block] {self.description}"
        return self.description

    def to_dict(self) -> dict[str, Any]:
        """Generic ``{name, description, input_schema}`` (no handler).

        A destructive tool's schema is rendered with the ``confirm`` argument
        the dispatch gate requires, so agents discover it instead of hitting a
        confirmation_required error first. The description carries the
        workflow/advanced lead tag (see :attr:`advertised_description`).
        """
        return {
            "name": self.name,
            "description": self.advertised_description,
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

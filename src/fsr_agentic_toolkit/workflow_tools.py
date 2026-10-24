"""The seven workflows as tools: one spec each, served by every surface.

The MCP server, the CLI and the SDK verbs share :mod:`fsr_agentic_toolkit.verbs.tools`'
registry and gate; the workflows join it here. :func:`register_into_framework`
also offers them to a host that runs fsr_playbooks' in-product dispatch (the SOC
assistant connector), withheld from its model until the host opts in.

Tiers follow fsr_playbooks' model: 1 is a read, 3 is a write that needs a human
yes (here: ``confirm: true``; on-box: an approval card).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .spec import ToolSpec, _obj

# name -> static tier. provision_module writes schema, publishes and grants a
# role, so it is the one that needs a human yes.
WORKFLOW_TIERS: dict[str, int] = {
    "debug_run": 1,
    "verify_effect": 1,
    "orient": 1,
    "provision_module": 3,
    "repair_playbook": 1,  # edits a draft; pushing it is a separate confirmed call
    "investigate_alert": 1,
    "build_playbook": 1,  # gates a draft; pushing it is a separate confirmed call
}
WORKFLOW_TOOLS: tuple[str, ...] = tuple(WORKFLOW_TIERS)


def _bind(client: Any) -> None:
    """Point fsr_playbooks' live-client seam at the caller's client."""
    if client is None:
        return
    from fsr_playbooks.mcp_server import _shared

    _shared.set_live_client(client)


def _wf(run: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    def handler(client: Any, **kwargs: Any) -> dict[str, Any]:
        _bind(client)
        return run(**kwargs).to_tool()

    handler.__name__ = getattr(run, "__name__", "workflow")
    return handler


def _debug_run(*, playbook_or_id: str, yaml_text: str | None = None):
    from .workflows.debug import debug_run

    return debug_run(playbook_or_id, yaml_text=yaml_text)


def _verify_effect(*, module: str = "", uuid: str = "", fields: dict | None = None,
                   assertions: list | None = None):
    from .workflows.verify import verify_effect

    return verify_effect(module=module, uuid=uuid, fields=fields, assertions=assertions)


def _orient(*, goal: str = "", modules: list | None = None):
    from .workflows.orient import orient

    return orient(goal=goal, modules=modules)


def _provision_module(*, module: str, label: str = "", plural: str = "",
                      fields: list | None = None, roles: list | None = None,
                      probe_record: dict | None = None, check_only: bool = False):
    from .workflows.provision import provision_module

    return provision_module(module, label=label, plural=plural, fields=fields, roles=roles,
                            probe_record=probe_record, check_only=check_only)


def _repair_playbook(*, follow_up: str, yaml_text: str):
    from .workflows.repair import repair_playbook

    return repair_playbook(follow_up, yaml_text)


def _investigate_alert(*, iri: str):
    from .workflows.investigate import investigate_alert

    return investigate_alert(iri)


def _build_playbook(*, goal: str, yaml_text: str = "", modules: list | None = None):
    from .workflows.build import build_playbook

    return build_playbook(goal, yaml_text=yaml_text, modules=modules)


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}

WORKFLOW_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "debug_run",
        "Explain why a playbook run did what it did, with evidence. Give a playbook name (its "
        "latest failed run) or a run pk. Findings cite the run and its steps: the failing step "
        "and its error, and any step argument that does not render against the run's own vars "
        "(the first is the likely root cause). Pass the result id to repair_playbook.",
        _obj({"playbook_or_id": {**_STR, "description": "Playbook name, or a run pk / task id."},
              "yaml_text": {**_STR, "description": "The playbook YAML, to re-render its Jinja."}},
             ["playbook_or_id"]),
        _wf(_debug_run),
        workflow=True,
    ),
    ToolSpec(
        "verify_effect",
        "Read the box back after a write and report what actually changed. Name a record "
        "(module + uuid) and the field values the write should have set; any mismatch makes the "
        "result partial, naming the field. Use after create_record / update_record / a run.",
        _obj({"module": _STR, "uuid": _STR,
              "fields": {"type": "object", "description": "field -> expected value"},
              "assertions": {"type": "array", "items": {"type": "object"}}}),
        _wf(_verify_effect),
        workflow=True,
    ),
    ToolSpec(
        "orient",
        "What this box has for a goal, in one call: each named module's real field names and "
        "picklist values, whether the caller can read it (and if not, which break: unpublished, "
        "role or team, with the fix), and the configured connectors matching the goal.",
        _obj({"goal": _STR, "modules": _STRS}),
        _wf(_orient),
        workflow=True,
    ),
    ToolSpec(
        "provision_module",
        "Create a usable custom module in ONE call. Do NOT hand-roll create_module + publish + "
        "create_record + a role grant -- this does all of it and proves it: create, publish (only "
        "when nothing else is staged; a publish already in flight is waited out and reported, so "
        "you never sleep or poll), grant the role(s), then verify the caller can read and create "
        "there. check_only=true writes nothing and names the break (unpublished, role, team) and the fix.",
        _obj({"module": {**_STR, "description": "Module type, lowercase_with_underscores."},
              "label": _STR, "plural": _STR,
              "fields": {"type": "array", "items": {"type": "object"},
                         "description": "[{name, type[, picklist][, required][, label]}]"},
              "roles": _STRS, "probe_record": {"type": "object"},
              "check_only": {"type": "boolean"}}, ["module"]),
        _wf(_provision_module),
        mutating=True,
        destructive=True,
        workflow=True,
    ),
    ToolSpec(
        "repair_playbook",
        "Fix a playbook from a debug_run diagnosis: pass the debug_run result id as follow_up "
        "and the YAML. Mechanical fixes are applied and re-validated (data.yaml); the rest are "
        "listed for a hand edit. Publishing the fix is a separate call.",
        _obj({"follow_up": _STR, "yaml_text": _STR}, ["follow_up", "yaml_text"]),
        _wf(_repair_playbook),
        workflow=True,
    ),
    ToolSpec(
        "investigate_alert",
        "Gather an alert's evidence: read the record, pull its indicators, and list the "
        "enrichment lookups configured here for each (as next calls). It does not judge. "
        "For FortiSOAR's own native AI investigation use run_fortiai_investigation.",
        _obj({"iri": {**_STR, "description": "The alert IRI, /api/3/alerts/<uuid>."}}, ["iri"]),
        _wf(_investigate_alert),
        workflow=True,
    ),
    ToolSpec(
        "build_playbook",
        "Orient on the modules a playbook touches, then gate the draft YAML with "
        "verify_playbook and hand back the next calls (publish, then verify_effect on a test "
        "record). Without yaml_text it returns the facts to write the draft from.",
        _obj({"goal": _STR, "yaml_text": _STR, "modules": _STRS}, ["goal"]),
        _wf(_build_playbook),
        workflow=True,
    ),
)

assert {s.name for s in WORKFLOW_SPECS} == set(WORKFLOW_TIERS)
assert all((WORKFLOW_TIERS[s.name] >= 3) == s.destructive for s in WORKFLOW_SPECS)


def register_into_framework(*, offer_to_model: bool = False) -> None:
    """Register the workflows into fsr_playbooks' in-product registry (the SOC
    assistant's dispatch, tiers and approval cards). Withheld from the model's
    tool list unless ``offer_to_model``; dispatchable either way. Idempotent."""
    import inspect

    from fsr_playbooks.llm import tools as fw

    for spec in WORKFLOW_SPECS:
        tier = WORKFLOW_TIERS[spec.name]
        fw.TOOL_TIERS[spec.name] = tier
        if spec.name not in fw.SAFE_TOOLS:
            fw.SAFE_TOOLS.append(spec.name)

        def fn(_spec: ToolSpec = spec, **kwargs: Any) -> dict[str, Any]:
            return _spec.handler(None, **kwargs)

        fn.__doc__ = inspect.cleandoc(spec.description)
        fw.REGISTRY[spec.name] = fw.ToolSpec(
            name=spec.name, description=spec.description, input_schema=spec.input_schema,
            fn=fn, tier=tier, confirm_mode="approve" if tier >= 2 else "auto")
    fw.withhold_from_model(WORKFLOW_TOOLS, withheld=not offer_to_model)

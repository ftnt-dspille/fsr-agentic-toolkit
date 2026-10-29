"""The workflows as tools: one spec each, served by every surface.

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
    "debug_playbook_run": 1,
    "verify_write": 1,
    "survey_box": 1,
    "provision_module": 3,
    "repair_playbook": 1,  # edits a playbook's YAML; pushing it is a separate confirmed call
    "investigate_alert": 1,
    "build_playbook": 1,  # checks a playbook's YAML; pushing it is a separate confirmed call
    "build_connector": 1,  # scaffolds+validates locally; deploy is a separate pyfsr call
    "run_playbook_yaml": 2,  # publishes and runs a playbook; deletes only what it created
    "setup_connector": 2,  # installs/configures a connector; secrets by reference only
}
WORKFLOW_TOOLS: tuple[str, ...] = tuple(WORKFLOW_TIERS)

# Former workflow names, kept as hidden dispatch aliases after a rename so saved
# MCP calls, scripts and skills using the old name keep working. current -> olds.
WORKFLOW_ALIASES: dict[str, tuple[str, ...]] = {
    "debug_playbook_run": ("debug_run",),
    "verify_write": ("verify_effect",),
    "survey_box": ("orient",),
}


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


def _build_connector(*, directory: str, name: str = "", scaffold: dict | None = None):
    from .workflows.connector import build_connector

    return build_connector(directory, name=name, scaffold=scaffold)


def _run_playbook_yaml(*, yaml_text: str, playbook: str = "", inputs: dict | None = None,
                  env: dict | None = None, record_uuid: str = "", module: str = "",
                  answers: dict | None = None, timeout: float = 120,
                  overwrite_changed: bool = False, cleanup: bool = False):
    from .workflows.run import run_playbook_yaml

    return run_playbook_yaml(yaml_text, playbook=playbook, inputs=inputs, env=env,
                              record_uuid=record_uuid, module=module, answers=answers,
                        timeout=timeout, overwrite_changed=overwrite_changed, cleanup=cleanup)


def _setup_connector(*, connector: str, config_name: str = "Default", fields: dict | None = None,
                     secrets: dict | None = None, version: str = "", make_default: bool = False,
                     install: bool = False, check_only: bool = False, healthcheck: bool = True):
    from .workflows.connector_setup import setup_connector

    return setup_connector(connector, config_name=config_name, fields=fields, secrets=secrets,
                           version=version, make_default=make_default, install=install,
                           check_only=check_only, healthcheck=healthcheck)


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}

WORKFLOW_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "debug_playbook_run",
        "Explain why a playbook run did what it did, with evidence. Give a playbook name (its "
        "latest failed run) or a run pk. Findings cite the run and its steps: the failing step "
        "and its error, and any step argument that does not render against the run's own vars "
        "(the first is the likely root cause). When such an argument reads a connector step's "
        "output and yaml_text is supplied, it also names the paths that actually resolve for "
        "that op (from the grounded shape oracle). Pass the result id to repair_playbook.",
        _obj({"playbook_or_id": {**_STR, "description": "Playbook name, or a run pk / task id."},
              "yaml_text": {**_STR, "description": "The playbook YAML, to re-render its Jinja."}},
             ["playbook_or_id"]),
        _wf(_debug_run),
        workflow=True,
        aliases=("debug_run",),
    ),
    ToolSpec(
        "verify_write",
        "Read the box back after a write and report what actually changed. Name a record "
        "(module + uuid) and the field values the write should have set; any mismatch makes the "
        "result partial, naming the field. Use after create_record / update_record / a run.",
        _obj({"module": _STR, "uuid": _STR,
              "fields": {"type": "object", "description": "field -> expected value"},
              "assertions": {"type": "array", "items": {"type": "object"}}}),
        _wf(_verify_effect),
        workflow=True,
        aliases=("verify_effect",),
    ),
    ToolSpec(
        "survey_box",
        "What this box has for a goal, in one call: each named module's real field names and "
        "picklist values, whether the caller can read it (and if not, which break: unpublished, "
        "role or team, with the fix), and the configured connectors matching the goal.",
        _obj({"goal": _STR, "modules": _STRS}),
        _wf(_orient),
        workflow=True,
        aliases=("orient",),
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
        "Fix a playbook from a debug_playbook_run diagnosis: pass the debug_playbook_run result id as follow_up "
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
        "Orient on the modules a playbook touches, then check the playbook YAML with "
        "verify_playbook and hand back the next calls (publish, then verify_write on a test "
        "record). Without yaml_text it returns the facts to write the playbook from.",
        _obj({"goal": _STR, "yaml_text": _STR, "modules": _STRS}, ["goal"]),
        _wf(_build_playbook),
        workflow=True,
    ),
    ToolSpec(
        "build_connector",
        "Scaffold and/or statically validate a FortiSOAR connector directory, and say what to "
        "fix next. With scaffold={ops:[...]} and an empty directory it drops in a gate-clean "
        "skeleton (safe request helper, dispatch, health check). It then runs the domain gates "
        ""
        "code safety/robustness) and "
        "returns each blocking issue plus the next call; re-run it after each edit. Deploying is "
        "a separate pyfsr deploy_connector call. Needs no box until deploy.",
        _obj({"directory": {**_STR, "description": "Connector directory (existing or to scaffold)."},
              "name": {**_STR, "description": "Machine name when scaffolding (defaults to the dir name)."},
              "scaffold": {"type": "object",
                           "description": "Scaffold spec when the dir is empty: "
                                          "{name?, label?, publisher?, ops?:[{operation, method?, parameters?}]}"}},
             ["directory"]),
        _wf(_build_connector),
        workflow=True,
    ),
    ToolSpec(
        "run_playbook_yaml",
        "Run a playbook from its YAML in ONE call: compile + grounded gate, publish, trigger, "
        "wait, and read back every step's result. Do NOT chain compile_playbook_yaml, "
        "publish_playbook, schedule_playbook, trigger_schedule_now and wait_for_playbook_run by "
        "hand. A failed gate deploys nothing; a failed run comes back with its diagnosis "
        "(debug_playbook_run) attached. cleanup=true deletes the collection and playbooks THIS "
        "call created (never ones that already existed), for throwaway probes. For a playbook "
        "with a manual_input/approval gate pass answers={<prompt title or variable>: <value>}; "
        "for a record-scoped trigger pass record_uuid + module.",
        _obj({"yaml_text": {**_STR, "description": "The playbook collection YAML source."},
              "playbook": {**_STR, "description": "Which playbook to run when the YAML holds several."},
              "inputs": {"type": "object", "description": "Sent as vars.inputs on the run."},
              "env": {"type": "object",
                      "description": "Top-level trigger values; fills vars.input.params for a "
                                     "start step that declares inputVariables."},
              "record_uuid": _STR, "module": _STR,
              "answers": {"type": "object"},
              "timeout": {"type": "number", "description": "Seconds to wait for the run (default 120)."},
              "overwrite_changed": {"type": "boolean",
                                    "description": "Discard live UI-edited steps the YAML lacks."},
              "cleanup": {"type": "boolean"}}, ["yaml_text"]),
        _wf(_run_playbook_yaml),
        mutating=True,
        workflow=True,
    ),
    ToolSpec(
        "setup_connector",
        "Configure a connector in ONE call: (install it), save a named configuration, and "
        "healthcheck it. SECRETS NEVER GO THROUGH YOU: put plain values (server URL, port, "
        "verify_ssl) in `fields`, and for each password/API-key field give a REFERENCE in "
        "`secrets`, e.g. secrets={'api_key': {'env': 'FSR_SECRET_VIRUSTOTAL__API_KEY'}}. The operator sets "
        "that variable where the MCP server runs; this tool reads it and sends it straight to "
        "FortiSOAR. A literal value for a secret field is refused. Only variables named "
        "FSR_SECRET_<CONNECTOR>__* resolve, and only for that connector; {'file': '<connector>/<name>'} "
        "reads a file inside $FSR_SECRETS_DIR. The "
        "result names where each secret came from and never contains it. check_only=true "
        "validates the fields and resolves the references without writing. install=true "
        "installs from Content Hub when the connector is missing and needs confirm_install=true. A configuration with the "
        "same config_name is overwritten, and the result says so.",
        _obj({"connector": {**_STR, "description": "Connector machine name, e.g. virustotal."},
              "config_name": {**_STR, "description": "Name of this configuration (default 'Default')."},
              "fields": {"type": "object", "description": "Plain, NON-secret values by field name."},
              "secrets": {"type": "object",
                          "description": "Secret fields by name, each a reference: "
                                         "{'env': 'FSR_SECRET_<CONNECTOR>__<FIELD>'} or {'file': '<name>'}. Never a value."},
              "version": _STR,
              "make_default": {"type": "boolean"},
              "install": {"type": "boolean", "description": "Install from Content Hub if missing (also needs confirm_install=true)."},
              "confirm_install": {"type": "boolean", "description": "Required with install=true: installing runs third-party code on the box."},
              "check_only": {"type": "boolean", "description": "Validate and resolve; write nothing."},
              "healthcheck": {"type": "boolean", "description": "Check reachability after saving (default true)."}},
             ["connector"]),
        _wf(_setup_connector),
        mutating=True,
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
        # The framework's arg gate SKIPS unknown-key rejection for any **kwargs
        # fn (fsr_playbooks.llm.arg_gate: a VAR_KEYWORD signature reads as
        # "accepts anything"), so a bare handler wrapper would let the model send
        # junk keys straight through -- the connector's registry contract sweep
        # (test_tool_sweep, invariant V) rightly fails such a tool. Advertise the
        # input_schema's own keys as an explicit KEYWORD_ONLY signature: the gate
        # then enforces unknown-key / required / type checks against the schema,
        # while the real call still dispatches through **kwargs unchanged.
        props = (spec.input_schema or {}).get("properties") or {}
        fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None)
             for k in props])
        fw.REGISTRY[spec.name] = fw.ToolSpec(
            name=spec.name, description=spec.description, input_schema=spec.input_schema,
            fn=fn, tier=tier, confirm_mode="approve" if tier >= 2 else "auto")
    fw.withhold_from_model(WORKFLOW_TOOLS, withheld=not offer_to_model)

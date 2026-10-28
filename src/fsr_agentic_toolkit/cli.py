"""``fsr-agentic-toolkit``: the same tools as the MCP server, from a shell.

    fsr-agentic-toolkit init --url fortisoar.example.com --api-key <key>
    fsr-agentic-toolkit mcp                         # the MCP server (stdio)
    fsr-agentic-toolkit tools                       # what the gate lets through
    fsr-agentic-toolkit debug-playbook-run --playbook-or-id "Close Alert"
    fsr-agentic-toolkit survey-box --modules alerts,heists --json
    fsr-agentic-toolkit call search_records '{"module": "alerts", "limit": 5}'

Every workflow is a subcommand; its flags are the tool's arguments (objects and
lists as JSON, lists also comma-separated). Calls go through the one dispatch
and gate, and a workflow prints the same WorkflowResult the MCP tool returns
(text by default, ``--json`` for the tool envelope). The box comes from the
``FSR_*`` environment or ``--instance`` (an alias in ``~/.pyfsr/instances.toml``).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .workflow_tools import WORKFLOW_SPECS


def _client(instance: str | None) -> Any:
    from pyfsr.instances import InstanceRegistry

    return InstanceRegistry.load().client(instance)


def _value(raw: str, schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind in ("object", "array"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if kind == "array":
                return [p.strip() for p in raw.split(",") if p.strip()]
            raise
    if kind == "boolean":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if kind == "integer":
        return int(raw)
    if kind == "number":
        return float(raw)
    return raw


def _print_result(out: Any, as_json: bool) -> int:
    from .workflows.result import WorkflowResult, render_text

    if as_json or not isinstance(out, dict) or "workflow" not in out:
        print(json.dumps(out, indent=2, default=str))
        return 0 if not (isinstance(out, dict) and "error" in out) else 1
    result = WorkflowResult.model_validate({k: v for k, v in out.items() if k != "ok"})
    print(render_text(result))
    return 0 if result.status != "blocked" else 1


def _add_workflow(sub: argparse._SubParsersAction, spec: Any) -> None:
    name = spec.name.replace("_", "-")
    # former names stay as hidden argparse aliases after a rename
    old = [a.replace("_", "-") for a in getattr(spec, "aliases", ())]
    p = sub.add_parser(name, aliases=old, help=spec.description.split(". ")[0] + ".")
    props = spec.rendered_schema().get("properties") or {}
    required = set(spec.input_schema.get("required") or [])
    for arg, schema in props.items():
        p.add_argument(f"--{arg.replace('_', '-')}", dest=arg, required=arg in required,
                       metavar=str(schema.get("type", "value")).upper(),
                       help=schema.get("description"))
    p.add_argument("--instance", help="configured instance alias")
    p.add_argument("--json", action="store_true", help="print the tool envelope as JSON")
    p.set_defaults(func=_run_tool, tool=spec.name, schema_props=props)


def _run_tool(args: argparse.Namespace) -> int:
    from .verbs.tools import dispatch

    arguments = {k: _value(getattr(args, k), s) for k, s in args.schema_props.items()
                 if getattr(args, k, None) is not None}
    return _print_result(dispatch(_client(args.instance), args.tool, arguments), args.json)


def _call(args: argparse.Namespace) -> int:
    from .verbs.tools import dispatch

    arguments = json.loads(args.arguments) if args.arguments else {}
    return _print_result(dispatch(_client(args.instance), args.tool, arguments), args.json)


def _tools(args: argparse.Namespace) -> int:
    from .verbs.tools import enabled_tools

    for spec in enabled_tools():
        print(f"{spec.name:32} tier {spec.tier}  {spec.description.split('. ')[0]}")
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from .verbs.mcp import main as serve

    serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    from . import init_cmd

    parser = argparse.ArgumentParser(prog="fsr-agentic-toolkit", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    init_cmd.build_subparser(sub)
    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=_mcp)
    sub.add_parser("tools", help="list the tools the gate allows, with their tier").set_defaults(func=_tools)
    p_call = sub.add_parser("call", help="call any tool by name with JSON arguments")
    p_call.add_argument("tool")
    p_call.add_argument("arguments", nargs="?", default="")
    p_call.add_argument("--instance")
    p_call.add_argument("--json", action="store_true")
    p_call.set_defaults(func=_call)
    for spec in WORKFLOW_SPECS:
        _add_workflow(sub, spec)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

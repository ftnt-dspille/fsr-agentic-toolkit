"""debug_run: why a playbook run did what it did.

Resolves the run (playbook name -> its latest failed run, or a run pk/task id),
reads the runtime failure (failing step + error) through the one run-failure
implementation, re-renders every Jinja block of the playbook against the run's
own vars, and returns findings that cite the run and the steps. The first render
failure upstream of the failing step is reported as the likely root cause; the
runtime error is the symptom.
"""
from __future__ import annotations

import re
from typing import Any

from fsr_playbooks.mcp_server import _shared
from fsr_playbooks.run_failure import failure_of

from .result import WorkflowResult, WorkflowRun

# Diagnostic codes from diagnose_yaml_against_pb_execution that mean a template
# did not produce a value.
_BAD_RENDER = frozenset({"render_error", "render_threw", "empty_render", "step_missing",
                         "attribute_missing"})


_DOUBLE_IRI = re.compile(r"/api/3/[A-Za-z0-9_]+/+/?api/3/")
_STEP_REF = re.compile(r"vars\.steps\.([A-Za-z0-9_]+)")


def _connector_ops_by_key(yaml_text: str | None) -> dict[str, tuple[str, str]]:
    """Map each step's jinja key (name, spaces->underscores) to its
    (connector, operation), for connector steps only. Empty on any failure."""
    if not yaml_text:
        return {}
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[str, str]] = {}
    for pb in (doc.get("playbooks") or []):
        for s in (pb.get("steps") or []):
            if s.get("type") in ("connector", "connector_op"):
                conn, op = s.get("connector"), s.get("operation")
                if conn and op:
                    out[str(s.get("name", "")).strip().replace(" ", "_")] = (conn, op)
    return out


def _reference_fix(template: object, by_key: dict[str, tuple[str, str]]) -> dict | None:
    """If a failing template reads a connector step's output, the grounded,
    resolvable paths for that op (so the fix rides along with the diagnosis)."""
    if not isinstance(template, str) or not by_key:
        return None
    for key in _STEP_REF.findall(template):
        if key in by_key:
            connector, op = by_key[key]
            try:
                from fsr_playbooks.output_shape import grounded_help
                help_ = grounded_help(connector, op, step_name=key)
            except Exception:  # noqa: BLE001
                return None
            if help_.get("grounded") and help_.get("suggested_references"):
                return {"step": key, "connector": connector, "operation": op,
                        "paths": [r["path"] for r in help_["suggested_references"][:4]]}
    return None


def _implicated(rendered: Any, error: str | None) -> bool:
    """A rendered argument value that shows up inside the runtime error (a URL,
    an id) is the argument the failing call was built from."""
    if not error or not isinstance(rendered, str):
        return False
    val = rendered.strip()
    return len(val) >= 8 and val in error


def _diagnose(playbook_or_id: str, yaml_text: str | None) -> dict[str, Any]:
    from fsr_playbooks.mcp_server.tools_recipe import why_did_playbook_fail
    return why_did_playbook_fail(playbook_or_id, yaml_text=yaml_text)


def debug_run(playbook_or_id: str, yaml_text: str | None = None) -> WorkflowResult:
    run = WorkflowRun("debug_run")
    raw = _diagnose(playbook_or_id, yaml_text)
    if not isinstance(raw, dict) or (raw.get("ok") is False and not raw.get("pb_execution")):
        msg = (raw or {}).get("message") or (raw or {}).get("error") or "could not resolve the run"
        run.unknown(f"{(raw or {}).get('code') or 'unresolved'}: {msg}")
        run.next("list_playbook_runs(playbook=...)", "find the run to debug by pk", 1)
        return run.result("blocked")

    pk = str(raw.get("pb_execution") or "")
    status = raw.get("run_status")
    failing_step = raw.get("failing_step")
    error = raw.get("error_message")
    if not (failing_step or error) and pk.isdigit():
        # A run-id input skips the by-name enrichment; read the run itself.
        client = _shared._live_client()
        rf = failure_of(getattr(client, "playbooks", None), pk=pk)
        if rf:
            failing_step = failing_step or rf["failing_step"]
            error = error or rf["error_message"]
            status = status or rf["status"]
    run.data.update({"pb_execution": pk, "playbook": raw.get("playbook_name"),
                     "run_status": status})

    run_ev = run.evidence("run", pk or playbook_or_id,
                          f"status={status}" + (f"; error={error}" if error else ""))
    step_ev: str | None = None
    if failing_step:
        step_ev = run.evidence("step", str(failing_step), str(error or "")[:400])
        run.finding(f"The run failed at step {failing_step!r}"
                    + (f": {error}" if error else ""), [run_ev, step_ev], "high")
    elif status and str(status).lower() in ("failed", "finished_with_error"):
        run.finding(f"The run ended {status} without naming a failing step",
                    [run_ev], "medium")

    by_key = _connector_ops_by_key(yaml_text)
    bad = [d for d in (raw.get("step_diagnostics") or [])
           if isinstance(d, dict) and d.get("code") in _BAD_RENDER]
    for i, d in enumerate(bad):
        ev = run.evidence("step", f"{d.get('step')}:{d.get('arg_path')}",
                          f"{d.get('code')}: {d.get('message') or d.get('rendered') or ''}"
                          f" | template {str(d.get('template'))[:200]}")
        cites = [ev] + ([step_ev] if step_ev else [])
        role = "likely root cause" if i == 0 else "also wrong"
        run.finding(f"Step {d.get('step')!r} argument {d.get('arg_path')} does not render "
                    f"against the run's vars ({d.get('code')}): {role}", cites,
                    "high" if i == 0 else "medium")
        # If the template reads a connector step's output, ride the actual
        # resolvable paths along with the diagnosis -- the shape the compiler
        # can't infer but the grounded oracle measured from a real run.
        fix = _reference_fix(d.get("template"), by_key)
        if fix:
            run.finding(
                f"{fix['step']!r} is {fix['connector']}.{fix['operation']}; its output "
                f"resolves at: {', '.join(fix['paths'])}", [ev], "high")
    # Templates that rendered "fine" but produced the bad value the runtime error
    # names (live: an IRI pasted into a path that already had the module prefix,
    # giving /api/3/alerts//api/3/alerts/<uuid> and a 404).
    for d in raw.get("step_diagnostics") or []:
        if not isinstance(d, dict) or d.get("code") in _BAD_RENDER:
            continue
        rendered = d.get("rendered")
        doubled = isinstance(rendered, str) and bool(_DOUBLE_IRI.search(rendered))
        if not (doubled or _implicated(rendered, error)):
            continue
        ev = run.evidence("step", f"{d.get('step')}:{d.get('arg_path')}",
                          f"renders {str(rendered)[:200]!r} | template "
                          f"{str(d.get('template'))[:200]}")
        cites = [ev] + ([step_ev] if step_ev else [])
        if doubled:
            claim = (f"Step {d.get('step')!r} argument {d.get('arg_path')} builds a doubled "
                     f"IRI ({str(rendered)[:120]!r}): the template prefixes /api/3/<module>/ "
                     f"to a value that is already a full IRI; use the IRI as-is: likely root cause")
        else:
            claim = (f"Step {d.get('step')!r} argument {d.get('arg_path')} renders the exact "
                     f"value the failing call reports ({str(rendered)[:120]!r}): likely root cause")
        run.finding(claim, cites, "high")
        bad.append(d)
    for h in raw.get("hints") or []:
        run.finding(str(h), [run_ev], "medium")

    if not failing_step and not bad:
        run.unknown("neither the run record nor a Jinja re-render names a cause; "
                    "the failure is inside a connector operation or the platform")
    if run._findings:
        run.next(f"repair_playbook(follow_up='{run.id}')",
                 "edit the step(s) named above, then verify", 3)
    return run.result()

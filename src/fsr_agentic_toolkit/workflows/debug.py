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

# Diagnosis codes that mean "a failed run exists but the library could not
# decompile/enrich it" (its decompiler lives in the dev `tooling/` tree, absent
# from the published library). These -- and only these -- warrant reading the
# failure straight off the run record by name. Terminal codes like
# `no_failed_runs` mean there is nothing to debug and must block.
_DECOMPILE_UNUSABLE_CODES = frozenset({"decompile_import_failed", "decompile_failed"})


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


def _safe_grounded_help(connector: str, op: str, step_name: str) -> dict:
    """grounded_help for an op, or {} if the oracle is unavailable or errors."""
    try:
        from fsr_playbooks.output_shape import grounded_help
        return grounded_help(connector, op, step_name=step_name) or {}
    except Exception:  # noqa: BLE001 -- best effort; never raise out of a diagnosis
        return {}


def _static_prerun_findings(run, yaml_text: str) -> int:
    """Diagnose a draft from the grounded oracle, with no run to read.

    Two sources: the gate's required_fixes (a step-output reference that would
    render empty at run time -- precise when the library is current), and the
    resolvable paths for every connector output the draft references (works from
    the grounded store alone). Returns how many findings were added."""
    produced = 0
    try:
        from fsr_playbooks.mcp_server.tools_verify import verify_playbook
        fixes = [f for f in (verify_playbook(yaml_text).get("required_fixes") or [])
                 if isinstance(f, dict)]
    except Exception:  # noqa: BLE001 -- optional/older library; fall back to path help
        fixes = []
    for fx in fixes[:10]:
        ev = run.evidence("playbook", str(fx.get("step") or "draft"),
                          str(fx.get("message") or "")[:300])
        run.finding(str(fx.get("message") or fx), [ev], "high")
        produced += 1
    by_key = _connector_ops_by_key(yaml_text)
    for key in dict.fromkeys(_STEP_REF.findall(yaml_text)):  # referenced, order-stable, unique
        if key not in by_key:
            continue
        connector, op = by_key[key]
        help_ = _safe_grounded_help(connector, op, key)
        if help_.get("grounded") and help_.get("suggested_references"):
            paths = [r["path"] for r in help_["suggested_references"][:4]]
            ev = run.evidence("step", key, f"{connector}.{op} grounded output shape")
            run.finding(f"{key!r} is {connector}.{op}; its output resolves at: "
                        f"{', '.join(paths)}", [ev], "medium")
            produced += 1
    return produced


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


def _failure_by_name(playbook_or_id: str) -> dict[str, Any] | None:
    """Read a playbook's latest run failure straight off the run record, by name.

    The library's by-name path renders the playbook's Jinja against the run,
    which means decompiling the live playbook -- and that decompiler lives in the
    dev `tooling/` tree, not the published library, so a name lookup there can
    come back `decompile_import_failed` (No module named 'cli'). The Jinja
    re-render is a bonus; the core diagnosis (failing step + error) comes straight
    off the run record via the client we already hold, so fall back to that rather
    than surface an import error to the analyst.
    """
    if not playbook_or_id or playbook_or_id.isdigit():
        return None  # a run-id input already resolves a run; no name fallback
    client = _shared._live_client()
    if client is None:
        return None
    try:
        return failure_of(getattr(client, "playbooks", None), playbook=playbook_or_id)
    except Exception:  # noqa: BLE001 -- best effort; never raise out of a fallback
        return None


def debug_run(playbook_or_id: str, yaml_text: str | None = None) -> WorkflowResult:
    run = WorkflowRun("debug_run")
    raw = _diagnose(playbook_or_id, yaml_text)
    unusable = not isinstance(raw, dict) or (raw.get("ok") is False and not raw.get("pb_execution"))
    code = raw.get("code") if isinstance(raw, dict) else None
    # Only read the failure off the run record when the library could not
    # DECOMPILE/enrich the run -- its decompiler is unshipped (No module named
    # 'cli'). A clean "no failed run" / "not found" means there is genuinely
    # nothing to debug: it must block, not trigger a by-name scrape that could
    # latch onto some other run (env-dependent, so it hid in hermetic CI).
    if unusable and (code in _DECOMPILE_UNUSABLE_CODES or not isinstance(raw, dict)):
        rf = _failure_by_name(playbook_or_id)
        if rf and (rf.get("failing_step") or rf.get("error_message")):
            raw = {"ok": True, "pb_execution": str(rf.get("pk") or ""),
                   "playbook_name": playbook_or_id, "run_status": rf.get("status"),
                   "failing_step": rf.get("failing_step"),
                   "error_message": rf.get("error_message"),
                   "step_diagnostics": [], "hints": []}
            unusable = False
    if unusable:
        # No run to read (author-first flow: the draft was never run). Don't just
        # block -- diagnose statically from the grounded oracle when we have the
        # YAML, so debug_run is useful BEFORE the first run, not only after a
        # failure. This is the same analysis the gate runs; surfacing it here
        # means debug_run works whichever way the agent arrives.
        if yaml_text and _static_prerun_findings(run, yaml_text):
            run.data["mode"] = "static_prerun"
            run.next(f"repair_playbook(follow_up='{run.id}')",
                     "fix the reference(s) above, then re-gate the draft", 3)
            run.next("run the playbook, then debug_run by its run pk",
                     "to catch failures only a real run can show (connector/platform)", 1)
            return run.result("partial")
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

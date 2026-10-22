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

from .result import WorkflowResult, WorkflowRun
from fsr_playbooks.run_failure import failure_of

# Diagnostic codes from diagnose_yaml_against_pb_execution that mean a template
# did not produce a value.
_BAD_RENDER = frozenset({"render_error", "render_threw", "empty_render", "step_missing",
                         "attribute_missing"})


_DOUBLE_IRI = re.compile(r"/api/3/[A-Za-z0-9_]+/+/?api/3/")


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

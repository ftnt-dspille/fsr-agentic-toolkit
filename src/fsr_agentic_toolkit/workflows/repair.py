"""repair_playbook: turn a debug_run diagnosis into a corrected playbook.

Takes the debug_run result id (``follow_up``) and the playbook YAML. Each root
cause the diagnosis named either gets a mechanical fix (a doubled IRI: drop the
``/api/3/<module>/`` prefix in front of a value that is already an IRI) or is
reported as needing a hand edit. The repaired YAML is re-validated; pushing it
stays a separate, approval-carded step, followed by verify_effect.
"""
from __future__ import annotations

import re

from .result import WorkflowResult, WorkflowRun, load_follow_up

_PREFIX_BEFORE_JINJA = re.compile(r"/api/3/[A-Za-z0-9_]+/(?=\{\{)")


def _fix_doubled_iri(yaml_text: str, template: str) -> tuple[str, str] | None:
    """``(new_yaml, new_template)`` with the prefix dropped, or None."""
    fixed = _PREFIX_BEFORE_JINJA.sub("", template, count=1)
    if fixed == template or template not in yaml_text:
        return None
    return yaml_text.replace(template, fixed), fixed


def repair_playbook(follow_up: str, yaml_text: str) -> WorkflowResult:
    prior, err = load_follow_up(follow_up, workflow="debug_run")
    if err is not None:
        run = WorkflowRun("repair_playbook")
        run.unknown(f"{err['code']}: {err['message']}")
        return run.result("blocked")
    run = WorkflowRun("repair_playbook", follow_up=prior)
    if not yaml_text:
        run.unknown("pass the playbook YAML to repair")
        return run.result("blocked")

    new_yaml = yaml_text
    for f in prior.findings:
        if "likely root cause" not in f.claim:
            continue
        step_evs = [e for e in f.evidence if prior.evidence[e].kind == "step"
                    and "template " in prior.evidence[e].excerpt]
        if "doubled IRI" in f.claim and step_evs:
            template = prior.evidence[step_evs[0]].excerpt.split("template ", 1)[1].strip()
            out = _fix_doubled_iri(new_yaml, template)
            if out:
                new_yaml, fixed = out
                run.finding(f"Fixed the doubled IRI: {template!r} -> {fixed!r}",
                            f.evidence, "high")
                continue
        run.unknown(f"needs a hand edit (no mechanical fix): {f.claim}")

    if new_yaml == yaml_text:
        run.unknown("no change made")
        return run.result("blocked" if not run._findings else "partial")

    from fsr_playbooks.mcp_server.tools_compile import validate_yaml
    v = validate_yaml(new_yaml)
    ok = bool(v.get("ok", not v.get("errors")))
    vev = run.evidence("playbook", "validate_yaml(repaired)", f"ok={ok} {str(v.get('errors') or '')[:300]}")
    if ok:
        run.finding("the repaired playbook validates", [vev], "high")
    else:
        run.unknown(f"the repaired playbook does not validate: {v.get('errors')}")
    run.data["yaml"] = new_yaml
    run.next("verify_playbook(yaml_text=<data.yaml>) then update_playbook(...)",
             "push the fix (approval-carded)", 3)
    run.next("debug_run(...) or verify_effect(...) after the next run",
             "confirm the run now succeeds and has its effect", 1)
    return run.result()

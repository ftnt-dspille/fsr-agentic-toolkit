"""build_playbook: orient on the box, gate the draft, hand back the push.

Chains survey_box (the modules' real fields and picklists, the caller's access,
configured connectors) -> verify_playbook on the draft YAML (the pre-submit
gate) -> the next calls: push (approval-carded) then verify_write on a test
record. Authoring the YAML stays with the model (compose_playbook and the
compiler); this workflow decides whether the draft is deliverable here.
"""
from __future__ import annotations

from .orient import orient
from .result import WorkflowResult, WorkflowRun


def build_playbook(goal: str, yaml_text: str = "", modules: list[str] | None = None
                   ) -> WorkflowResult:
    facts = orient(goal=goal, modules=modules or [], connectors=bool(goal))
    run = WorkflowRun("build_playbook", follow_up=facts)
    run.data["survey_box_id"] = facts.id
    for u in facts.unknowns:
        run.unknown(f"survey_box: {u}")
    blocked_modules = [m for m, info in (facts.data.get("modules") or {}).items()
                       if (info.get("access") or {}).get("read") not in ("ok", None)]
    for m in blocked_modules:
        run.next(f"provision_module(module='{m}')",
                 f"the caller cannot use {m}; a playbook writing to it would fail", 3)

    if not yaml_text:
        run.unknown("no draft yet: write the YAML from the fields above "
                    "(compose_playbook), then call build_playbook again with it")
        run.next("playbook_yaml_reference()", "the YAML shape and step types to write the draft from", 1)
        run.next("compose_playbook(...)", "draft the playbook", 0)
        return run.result("partial" if facts.findings else "blocked")

    from fsr_playbooks.mcp_server.tools_verify import verify_playbook
    v = verify_playbook(yaml_text)
    ready = bool(v.get("ready_to_push"))
    fixes = v.get("required_fixes") or []
    vev = run.evidence("playbook", "verify_playbook(draft)",
                       f"ready_to_push={ready}; required_fixes={len(fixes)}; "
                       f"warnings={len(v.get('warnings') or [])}")
    if ready:
        run.finding("the draft passes the pre-submit gate", [vev], "high")
        run.next("push_playbook / emit_card(card_type='playbook_offer')",
                 "deploy it (approval-carded)", 3)
        run.next("verify_write(module=..., uuid=<test record>, fields=...)",
                 "create a test record and check the playbook changed it", 1)
    else:
        for fx in fixes[:10]:
            msg = fx.get("message") if isinstance(fx, dict) else str(fx)
            run.finding(f"must fix before pushing: {msg}", [vev], "high")
        run.next("playbook_yaml_reference(topic=<step type>)", "the accepted keys for the step to fix", 1)
        run.next("build_playbook(goal, yaml_text=<fixed>)", "re-gate the fixed draft", 0)
    run.data["verify"] = {"ready_to_push": ready, "required_fixes": fixes[:10]}
    if blocked_modules or not ready:
        return run.result("partial")
    return run.result()

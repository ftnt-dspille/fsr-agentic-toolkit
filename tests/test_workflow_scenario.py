"""Mechanics of the workflow-requiring scenario: the world, the grader, both paths.

No LLM: scripted fake models walk the bare path and the workflow path, and a
set of deliberately wrong paths prove the grader can fail (a grader that cannot
fail measures nothing). Needs the agentic toolkit on the path; skipped (not
passed) without it.
"""
from __future__ import annotations

import itertools

import pytest
import workflow_scenario as W

FIX = {"operations": [{"op": "update_step", "name": W.FAILING_STEP,
                       "set": {"params.iri": "{{ vars.steps.Find_Latest_Alert[0]['@id'] }}"}}]}
ANSWER = (f"Run {W.FAILED_RUN} failed at {W.FAILING_STEP}: the argument prefixed "
          f"/api/3/alerts/ to a value that is already a full IRI (doubled path). I fixed "
          f"it, re-ran, and read the alert back: {W.FIELD} is {W.REFRESHED}.")


def script(steps):
    """A fake model that plays `steps` (list of (tool, args) or a final str)."""
    it = iter(steps)
    ids = itertools.count()

    def model(_messages, _tools):
        s = next(it)
        if isinstance(s, str):
            return {"content": s, "tool_calls": []}
        name, args = s
        return {"content": None, "tool_calls": [
            {"id": f"c{next(ids)}", "name": name, "arguments": args}]}

    return model


def test_bare_path_passes():
    g, _, box = W.drive(script([
        ("why_did_playbook_fail", {"playbook_or_id": W.PLAYBOOK}),
        ("edit_playbook", FIX),
        ("run_playbook", {"playbook": W.PLAYBOOK}),
        ("get_record", {"module": "alerts", "uuid": W.ALERT_UUID}),
        ANSWER]), "hidden")
    assert g.passed and not g.workflow_calls
    assert box.record[W.FIELD] == W.REFRESHED


def test_workflow_path_passes_and_cites_evidence():
    box_holder = {}

    class M:
        def __init__(self):
            self.n = 0

        def __call__(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return {"content": None, "tool_calls": [{"id": "a", "name": "debug_run",
                        "arguments": {"playbook_or_id": W.PLAYBOOK}}]}
            if self.n == 2:
                import json
                res = json.loads(messages[-1]["content"])
                box_holder["id"] = res["id"]
                return {"content": None, "tool_calls": [{"id": "b", "name": "repair_playbook",
                        "arguments": {"follow_up": res["id"], "yaml_text": W.YAML_TEXT}}]}
            if self.n == 3:
                return {"content": None, "tool_calls": [{"id": "c", "name": "edit_playbook",
                                                         "arguments": FIX}]}
            if self.n == 4:
                return {"content": None, "tool_calls": [{"id": "d", "name": "run_playbook",
                        "arguments": {"playbook": W.PLAYBOOK}}]}
            if self.n == 5:
                return {"content": None, "tool_calls": [{"id": "e", "name": "verify_effect",
                        "arguments": {"module": "alerts", "uuid": W.ALERT_UUID,
                                      "fields": {W.FIELD: W.REFRESHED}}}]}
            return {"content": ANSWER + " evidence e1", "tool_calls": []}

    g, _, _ = W.drive(M(), "offered")
    assert g.passed and g.workflow_calls == ["debug_run", "repair_playbook", "verify_effect"]
    assert g.cited_evidence_id


def test_hidden_arm_does_not_advertise_workflows_and_offered_does():
    hidden = {t["function"]["name"] for t in W.tool_list("hidden")}
    offered = {t["function"]["name"] for t in W.tool_list("offered")}
    assert not hidden & set(W.WORKFLOW_NAMES)
    assert {"debug_run", "repair_playbook", "verify_effect"} <= offered


@pytest.mark.parametrize("steps,field", [
    # never fixed: the arm that stops at diagnosis
    ([("why_did_playbook_fail", {"playbook_or_id": W.PLAYBOOK}), ANSWER], "fix_pushed"),
    # fixed and re-run but never read the record back
    ([("edit_playbook", FIX), ("run_playbook", {"playbook": W.PLAYBOOK}), ANSWER],
     "read_back"),
    # a wrong fix (drops the argument): the run still fails, no effect
    ([("edit_playbook", {"operations": [{"op": "update_step", "name": W.FAILING_STEP,
                                         "set": {"params.iri": "/api/3/alerts/x"}}]}),
      ("run_playbook", {"playbook": W.PLAYBOOK}),
      ("get_record", {"module": "alerts", "uuid": W.ALERT_UUID}), ANSWER], "effect"),
    # correct work, answer cites nothing
    ([("edit_playbook", FIX), ("run_playbook", {"playbook": W.PLAYBOOK}),
      ("get_record", {"module": "alerts", "uuid": W.ALERT_UUID}), "Fixed it."], "cites"),
])
def test_grader_fails_each_broken_path(steps, field):
    g, _, _ = W.drive(script(steps), "hidden")
    assert not g.passed and getattr(g, field) is False

"""debug_run rides the resolvable reference paths along with its diagnosis.

When a step's argument fails to render and the template reads a connector step's
output, debug_run now attaches the grounded, resolvable paths for that op -- the
fix the agent otherwise has to rediscover. Folded into the existing tool, not a
new one. The diagnosis itself is stubbed; this pins the enrichment.
"""
from __future__ import annotations

from fsr_agentic_toolkit.workflows import debug

_YAML = """\
collection: G
playbooks:
- name: P
  steps:
  - {name: Start, type: start, next: Get Macros}
  - {name: Get Macros, type: connector, connector: cyops_utilities, operation: get_macro_list, next: Record}
  - name: Record
    type: create_record
    module: alerts
    resource: {name: D, description: "{{ vars.steps.Get_Macros.data.macros }}"}
"""


def _fake_diag(playbook_or_id, yaml_text=None):
    # a render failure on the Record step's description, reading Get_Macros.
    return {
        "ok": True, "pb_execution": "1001", "run_status": "finished",
        "failing_step": None, "error_message": None,
        "step_diagnostics": [
            {"step": "Record", "arg_path": "arguments.resource.description",
             "code": "empty_render", "rendered": "",
             "template": "{{ vars.steps.Get_Macros.data.macros }}"},
        ],
        "hints": [],
    }


def test_debug_run_attaches_resolvable_paths(monkeypatch):
    monkeypatch.setattr(debug, "_diagnose", _fake_diag)
    res = debug.debug_run("P", yaml_text=_YAML).to_tool()
    # the enrichment finding names the op and the paths that actually resolve
    fix_findings = [str(f) for f in res.get("findings", []) if "resolves at:" in str(f)]
    assert fix_findings, res.get("findings")
    line = fix_findings[0]
    assert "cyops_utilities.get_macro_list" in line
    assert "vars.steps.Get_Macros.data | join" in line
    # the suggested paths are the resolvable ones, not the broken phantom attr
    suggested = line.split("resolves at:", 1)[1]
    assert ".data.macros" not in suggested


def test_no_enrichment_without_yaml(monkeypatch):
    monkeypatch.setattr(debug, "_diagnose", _fake_diag)
    res = debug.debug_run("P").to_tool()  # no yaml_text -> no step->op map
    assert "resolves at:" not in str(res)

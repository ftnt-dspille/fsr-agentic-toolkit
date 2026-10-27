"""debug_run diagnoses a draft from the grounded oracle with no run to read.

The author-first flow has no failed run, so the library blocks (no_failed_run_
provider). debug_run must not dead-end there: when it has the YAML it surfaces
the grounded resolvable paths for each connector output the draft references, so
the agent gets the fix before the first run -- not only after a failure.
"""
from __future__ import annotations

from fsr_agentic_toolkit.workflows import debug

_YAML = """\
collection: G
playbooks:
- name: P
  steps:
  - {name: Start, type: start, next: Get Macros}
  - {name: Get Macros, type: connector, connector: cyops_utilities, operation: get_macro_list, next: Rec}
  - name: Rec
    type: create_record
    module: alerts
    resource: {name: D, description: "{{ vars.steps.Get_Macros.data.value | join(', ') }}"}
"""

# The library cannot find a run by name here (no failed-run provider in this path).
_BLOCKED = {"ok": False, "code": "no_failed_run_provider", "message": "needs a run pk"}


def test_no_run_but_yaml_gives_static_grounded_findings(monkeypatch):
    monkeypatch.setattr(debug, "_diagnose", lambda *_a, **_k: dict(_BLOCKED))
    res = debug.debug_run("P", yaml_text=_YAML).to_tool()
    assert res.get("status") != "blocked", res
    paths = [str(f) for f in res.get("findings", []) if "resolves at:" in str(f)]
    assert paths, res.get("findings")
    assert "cyops_utilities.get_macro_list" in paths[0]
    assert res.get("data", {}).get("mode") == "static_prerun"


def test_no_run_and_no_yaml_still_blocks(monkeypatch):
    monkeypatch.setattr(debug, "_diagnose", lambda *_a, **_k: dict(_BLOCKED))
    res = debug.debug_run("P").to_tool()
    assert res.get("status") == "blocked", res
    assert not [f for f in res.get("findings", []) if "resolves at:" in str(f)]

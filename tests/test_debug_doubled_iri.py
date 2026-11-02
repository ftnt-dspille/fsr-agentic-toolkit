"""debug_run names a doubled IRI from the run's own error plus the YAML, even when
the library's render diagnostics are unavailable (its run-env probes are not
shipped), so repair_playbook gets the root cause it knows how to fix."""
from __future__ import annotations

from fsr_agentic_toolkit.workflows import debug, repair

_YAML = """\
collection: G
playbooks:
- name: P
  steps:
  - {name: Start, type: start_on_create, module: alerts, next: Tag Alert}
  - name: Tag Alert
    type: update_record
    module: alerts
    record: "/api/3/alerts/{{ vars.input.records[0]['@id'] }}"
    fields: {description: x}
"""

_ERR = 'No route found for "PUT https://localhost/api/3/alerts//api/3/alerts/abc" failed with status code 404'


def _fake_diag(playbook_or_id, yaml_text=None):
    return {"ok": True, "pb_execution": "516", "run_status": "failed",
            "failing_step": "Tag Alert", "error_message": _ERR,
            "step_diagnostics": [], "hints": []}


def test_doubled_iri_found_and_repaired(monkeypatch):
    monkeypatch.setattr(debug, "_diagnose", _fake_diag)
    res = debug.debug_run("516", yaml_text=_YAML)
    claims = [f.claim for f in res.findings]
    assert any("doubled IRI" in c and "likely root cause" in c for c in claims), claims

    fixed = repair.repair_playbook(res.id, _YAML)
    assert fixed.data["yaml"].count("/api/3/alerts/{{") == 0
    assert "record: \"{{ vars.input.records[0]['@id'] }}\"" in fixed.data["yaml"]


def test_no_doubled_iri_claim_when_the_error_is_not_doubled(monkeypatch):
    def diag(playbook_or_id, yaml_text=None):
        d = _fake_diag(playbook_or_id, yaml_text)
        d["error_message"] = "status code 404 on /api/3/alerts/abc"
        return d

    monkeypatch.setattr(debug, "_diagnose", diag)
    res = debug.debug_run("516", yaml_text=_YAML)
    assert not any("doubled IRI" in f.claim for f in res.findings)

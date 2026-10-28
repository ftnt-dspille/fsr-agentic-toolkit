"""T1 workflows: debug_run names the seeded defect; verify_effect catches a silent no-op.

Graders check the ANSWER (the finding names the defect, the missing field is a
finding), not that a tool was reached. No box: the run diagnosis and the record
reads are fakes shaped like recorded live responses.
"""
from __future__ import annotations

import pytest
from fsr_playbooks.mcp_server import _shared
from fsr_playbooks.run_failure import failure_of

from fsr_agentic_toolkit.workflows import MemoryResultStore, load_follow_up, set_result_store
from fsr_agentic_toolkit.workflows import debug as D
from fsr_agentic_toolkit.workflows.verify import verify_effect

_IRI = "/api/3/alerts/1daad405-3999-4682-ba60-586bec063d25"
_DOUBLE_ERROR = (f"CS-INTEGRATION-5: 404 Client Error: Not Found for url: "
                 f"https://localhost/api/3/alerts/{_IRI}")


@pytest.fixture(autouse=True)
def _store():
    set_result_store(MemoryResultStore())


def _diag(**over):
    base = {
        "ok": False, "pb_execution": "122386", "run_status": "failed",
        "playbook_name": "Refresh Latest Alert Details",
        "failing_step": "Get Alert Details", "error_message": _DOUBLE_ERROR,
        "step_diagnostics": [
            {"step": "Find Latest Alert", "arg_path": "query", "ok": True,
             "code": "ok", "template": "{{ vars.input }}", "rendered": "x"},
            {"step": "Get Alert Details", "arg_path": "params.iri", "ok": True,
             "code": "ok", "template": "/api/3/alerts/{{ vars.steps.Find_Latest_Alert[0]['@id'] }}",
             "rendered": f"/api/3/alerts/{_IRI}"},
        ],
        "hints": [],
    }
    base.update(over)
    return base


# --- debug_run grader: the recorded double-IRI failure -------------------------

def test_debug_run_names_the_doubled_iri_as_the_root_cause(monkeypatch):
    monkeypatch.setattr(D, "_diagnose", lambda p, y: _diag())
    r = D.debug_run("Refresh Latest Alert Details")
    assert r.status == "answered"
    claims = [f.claim for f in r.findings]
    assert any("failed at step 'Get Alert Details'" in c for c in claims)
    root = [f for f in r.findings if "likely root cause" in f.claim]
    assert len(root) == 1 and "doubled IRI" in root[0].claim
    assert "params.iri" in root[0].claim
    # The finding cites the step evidence AND the failing step.
    assert len(root[0].evidence) == 2 and all(e in r.evidence for e in root[0].evidence)
    assert r.next and r.next[0].action.startswith("repair_playbook(follow_up=")


def test_debug_run_flags_a_render_failure_first_as_root_cause(monkeypatch):
    diag = _diag(error_message="KeyError", step_diagnostics=[
        {"step": "Enrich", "arg_path": "ip", "ok": False, "code": "step_missing",
         "template": "{{ vars.steps.Lookup.ip }}", "message": "no Lookup in env"},
        {"step": "Close", "arg_path": "note", "ok": False, "code": "render_error",
         "template": "{{ x | bad }}", "message": "no filter bad"},
    ])
    monkeypatch.setattr(D, "_diagnose", lambda p, y: diag)
    r = D.debug_run("122386")
    roots = [f for f in r.findings if "likely root cause" in f.claim]
    assert len(roots) == 1 and "'Enrich'" in roots[0].claim
    assert any("'Close'" in f.claim and "also wrong" in f.claim for f in r.findings)


def test_debug_run_says_what_it_could_not_find(monkeypatch):
    monkeypatch.setattr(D, "_diagnose", lambda p, y: _diag(
        failing_step=None, error_message=None, run_status="finished", step_diagnostics=[]))
    monkeypatch.setattr(_shared, "_live_client", lambda: None)
    r = D.debug_run("122386")
    assert r.status == "blocked" and r.unknowns


def test_debug_run_blocked_when_the_run_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(D, "_diagnose", lambda p, y: {
        "ok": False, "code": "no_failed_runs", "message": "no recent failed run"})
    r = D.debug_run("Nope")
    assert r.status == "blocked" and "no_failed_runs" in r.unknowns[0]


def test_debug_run_falls_back_to_the_run_record_when_decompile_is_unshipped(monkeypatch):
    """The published library has no decompiler, so a by-name lookup can return
    `decompile_import_failed` (No module named 'cli'). debug_run must still name
    the failing step + error by reading the run record, not block on the import.
    """
    monkeypatch.setattr(D, "_diagnose", lambda p, y: {
        "ok": False, "code": "decompile_import_failed",
        "message": "ModuleNotFoundError(\"No module named 'cli'\")"})
    monkeypatch.setattr(_shared, "_live_client", lambda: object())
    monkeypatch.setattr(D, "failure_of", lambda pbs, **kw: {
        "failing_step": "Tag Alert", "status": "failed", "pk": "10638",
        "error_message": "404 Not Found for url: /api/3/alerts/00000000-0000-0000-0000-000000000000"})
    r = D.debug_run("Tag Agent Lab Alerts")
    assert r.status == "answered"
    claims = [f.claim for f in r.findings]
    assert any("failed at step 'Tag Alert'" in c for c in claims)
    assert any("404" in c for c in claims)
    assert "No module named" not in " ".join(u for u in r.unknowns)
    assert r.next and r.next[0].action.startswith("repair_playbook(follow_up=")


def test_debug_run_still_blocks_when_the_run_record_is_also_empty(monkeypatch):
    """No decompiler AND no readable failure -> block cleanly (not a traceback)."""
    monkeypatch.setattr(D, "_diagnose", lambda p, y: {
        "ok": False, "code": "decompile_import_failed", "message": "No module named 'cli'"})
    monkeypatch.setattr(_shared, "_live_client", lambda: object())
    monkeypatch.setattr(D, "failure_of", lambda pbs, **kw: None)
    r = D.debug_run("Tag Agent Lab Alerts")
    assert r.status == "blocked" and r.unknowns


def test_debug_run_result_is_a_follow_up_handle(monkeypatch):
    monkeypatch.setattr(D, "_diagnose", lambda p, y: _diag())
    r = D.debug_run("x")
    found, err = load_follow_up(r.id, workflow="debug_playbook_run")
    assert err is None and found == r


# --- one run-failure implementation --------------------------------------------

class _PB:
    def __init__(self, by_pk=None, by_name=None, boom=False):
        self.by_pk, self.by_name, self.boom = by_pk, by_name, boom

    def run_failure(self, pk):
        if self.boom:
            raise RuntimeError("box blinked")
        return self.by_pk

    def why_failed(self, playbook):
        return self.by_name


class _Typed:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_failure_of_reads_dicts_and_typed_models_the_same():
    d = {"failing_step": "S", "error_message": "E", "status": "failed"}
    assert failure_of(_PB(by_pk=d), pk="7") == {**d, "pk": "7"}
    t = _Typed(failing_step="S", error_message="E", status="failed", pk=9)
    assert failure_of(_PB(by_name=t), playbook="P") == {**d, "pk": "9"}
    assert failure_of(_PB(boom=True), pk="7") is None
    assert failure_of(None, pk="7") is None
    assert failure_of(_PB(by_pk={}), pk="7") is None


def test_run_playbook_failure_detail_parity():
    """run_playbook's failure detail keeps its old shape through the shared reader."""
    from fsr_playbooks.mcp_server.tools_playbook_run import _run_failure_detail
    pb = _PB(by_pk={"failing_step": "S", "error_message": "E", "status": "failed"})
    assert _run_failure_detail(pb, {"@id": "/wf/api/workflows/42/"}) == {
        "failing_step": "S", "error_message": "E", "pk": "42"}
    assert _run_failure_detail(_PB(by_pk={"status": "failed"}), {"pk": 42}) is None
    assert _run_failure_detail(pb, {}) is None


# --- verify_effect grader: a silent no-op write --------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Client:
    base_url = ""


def _box(monkeypatch, record, status=200):
    monkeypatch.setattr(_shared, "_live_client", lambda: _Client())
    monkeypatch.setattr(_shared, "live_request_with_reauth",
                        lambda m, c, url, **k: _Resp(status, record))


def test_verify_effect_catches_a_write_that_changed_nothing(monkeypatch):
    # The write sent outputTemplateJson (wrong case); the platform answered 200
    # and stored nothing. The record still has the old value.
    _box(monkeypatch, {"uuid": "u", "outputTemplateJSON": "[]",
                       "severity": {"itemValue": "High", "@id": "/api/3/picklists/h"}})
    r = verify_effect("ztpf_templates", "u",
                      {"outputTemplateJSON": "[1]", "severity": "High",
                       "outputTemplateJson": "[1]"})
    assert r.status == "partial"
    by_field = {f.claim.split(" ")[0].strip("'"): f.claim for f in r.findings}
    assert "expected '[1]'" in by_field["outputTemplateJSON"]
    assert "as expected" in by_field["severity"]
    assert "not a field" in by_field["outputTemplateJson"]
    assert r.next


def test_verify_effect_answered_when_every_field_matches(monkeypatch):
    _box(monkeypatch, {"name": "x", "owner": {"@id": "/api/3/people/p1"}})
    r = verify_effect("alerts", "u", {"name": "X", "owner": "/api/3/people/p1"})
    assert r.status == "answered" and len(r.findings) == 2


def test_verify_effect_cannot_read_the_record(monkeypatch):
    _box(monkeypatch, {}, status=404)
    r = verify_effect("alerts", "u", {"name": "x"})
    assert r.status == "blocked" and "HTTP 404" in r.unknowns[0]
    assert not r.evidence  # a failed read is not citable evidence


def test_verify_effect_needs_a_target():
    assert verify_effect().status == "blocked"


def test_workflow_tools_register_into_the_framework_read_only():
    from fsr_playbooks.llm.tools import REGISTRY

    from fsr_agentic_toolkit.workflow_tools import register_into_framework
    register_into_framework()
    for n in ("debug_playbook_run", "verify_write"):
        assert REGISTRY[n].tier == 1 and REGISTRY[n].confirm_mode == "auto"
    assert REGISTRY["provision_module"].tier == 3
    assert REGISTRY["provision_module"].confirm_mode == "approve"


def test_workflows_are_offered_to_the_model_only_when_a_host_opts_in():
    from fsr_playbooks.llm import tools as T

    from fsr_agentic_toolkit.workflow_tools import register_into_framework
    names = lambda: {t["name"] for t in T.anthropic_tools()}
    register_into_framework()
    assert "debug_playbook_run" not in names()
    register_into_framework(offer_to_model=True)
    try:
        assert {"debug_playbook_run", "verify_write"} <= names()
    finally:
        register_into_framework()
    assert "debug_playbook_run" not in names()
    assert "debug_playbook_run" in T.REGISTRY  # still dispatchable

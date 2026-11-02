"""Guards that stop an agent from guessing: each one answers a question the agent
used to have to work out (or got wrong) on a live box."""
from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pyfsr.exceptions import FortiSOARException

from fsr_agentic_toolkit.verbs import tools as T

# The shape the platform returns: a date stores as "integer"; only form_type says date.
FIELDS = [{"name": "name", "type": "string", "form_type": "text"},
          {"name": "status", "type": "picklists", "form_type": "picklist"},
          {"name": "dueDate", "type": "integer", "form_type": "datetime"},
          {"name": "flag", "type": "boolean", "form_type": "checkbox"},
          {"name": "count", "type": "integer", "form_type": "integer"},
          {"name": "ip", "type": "string", "form_type": "ipv4"},
          {"name": "mail", "type": "string", "form_type": "email"},
          {"name": "blob", "type": "object", "form_type": "json"}]


class _Records:
    def __init__(self, store):
        self.store = store
        self.queried = None

    def query(self, body):
        self.queried = body
        return {"hydra:member": []}

    def prepare_values(self, data):
        from pyfsr.exceptions import ValidationError
        from pyfsr.field_values import check_record, unknown_fields_problem
        forms = {f["name"]: f["form_type"] for f in FIELDS}
        if problem := unknown_fields_problem(forms, data):
            raise ValidationError(f"refused before writing for alerts: {problem}")
        out, self.last_conversions = check_record(forms, data)
        return out

    from pyfsr.records import RecordSet

    verify_write = RecordSet.verify_write

    def create(self, data, **_):
        self.created = data
        rec = {"@id": "/api/3/alerts/u1", "uuid": "u1", **data}
        # The box keeps only the fields it knows.
        self.store["u1"] = {k: v for k, v in rec.items() if k in {"@id", "uuid", "name", "status", "dueDate"}}
        return rec

    def get(self, ref, **_):
        return self.store.get(str(ref).rsplit("/", 1)[-1], {"@id": ref, "name": "x"})


class _Client:
    def __init__(self):
        self.store: dict = {}
        self.recs = _Records(self.store)
        self.modules_asked: list[str] = []

    def describe_module(self, module):
        return {"fields": FIELDS}

    def records(self, module):
        self.modules_asked.append(module)
        return self.recs


# -- writes -----------------------------------------------------------------
def test_pyfsr_conversions_are_reported_and_read_back():
    # The rules live in pyfsr (pyfsr.field_values); the toolkit runs them up front.
    c = _Client()
    out = T.dispatch(c, "create_record", {"module": "alerts",
                                          "data": {"name": "a", "dueDate": "2026-01-02T03:04:05Z"}})
    want = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp())
    assert c.store["u1"]["dueDate"] == want
    assert out["converted"] == ["dueDate: ISO date -> epoch seconds"]
    assert out["readback"]["verified"] is True  # compared against the converted value


def test_a_pyfsr_refusal_is_an_agent_error_and_nothing_is_written():
    c = _Client()
    out = T.dispatch(c, "create_record", {"module": "alerts", "data": {"name": "a", "flag": "maybe"}})
    assert "not true/false" in out["error"]["message"]
    assert "u1" not in c.store


def test_an_unknown_field_is_refused_by_pyfsr_and_nothing_is_written():
    c = _Client()
    out = T.dispatch(c, "create_record", {"module": "alerts", "data": {"name": "a", "nosuchfield": 1}})
    assert "'nosuchfield' is not a field" in out["error"]["message"]
    assert "u1" not in c.store


def test_a_clean_write_reads_back_verified():
    out = T.dispatch(_Client(), "create_record", {"module": "alerts", "data": {"name": "a"}})
    assert out["readback"] == {"verified": True}


def test_get_record_takes_the_module_from_an_iri():
    c = _Client()
    T.dispatch(c, "get_record", {"ref": "/api/3/incidents/u9"})
    assert c.modules_asked == ["incidents"]
    out = T.dispatch(c, "get_record", {"ref": "u9"})
    assert "pass module" in out["error"]["message"]


# -- FortiAI investigations -------------------------------------------------
class _AI:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def start_alert_investigation(self, ref):
        return SimpleNamespace(task_id="inv-1", status="pending")

    def get_status(self, task_id):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def get_result(self, task_id):
        from pyfsr.models._ai import InvestigationResult
        return InvestigationResult.model_validate({"verdict": "benign"})

    from pyfsr.api.ai import AIApi

    wait_for_result = AIApi.wait_for_result


def test_wait_blocks_until_the_investigation_finishes(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = SimpleNamespace(ai=_AI(["pending", "inprogress", "completed"]))
    out = T.dispatch(c, "run_fortiai_investigation", {"ref": "/api/3/alerts/u1", "wait": True})
    assert out["investigation_task_id"] == "inv-1"
    assert out["status"] == "completed" and out["result"]["verdict"] == "benign"


def test_an_unfinished_investigation_says_so(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = SimpleNamespace(ai=_AI(["inprogress"]))
    out = T.dispatch(c, "run_fortiai_investigation", {"ref": "/api/3/alerts/u1", "wait": True, "timeout": 0})
    assert out["finished"] is False and out["timed_out"] is True
    assert "get_investigation_result(task_id='inv-1', wait=true)" in out["next"]
    assert "result" not in out


# -- registered MCP tools ---------------------------------------------------
def _mcp_client(result):
    res = SimpleNamespace(ok=True, status=None, error=None, result=result)
    return SimpleNamespace(ai=SimpleNamespace(call_registered_tool=lambda *a, **k: res))


def test_a_tool_level_mcp_error_is_not_ok():
    body = {"isError": True, "content": [{"type": "text", "text": "repo not found"}]}
    out = T.dispatch(_mcp_client(body), "call_registered_mcp_tool",
                     {"server": "s", "tool": "t", "confirm": True})
    assert out["ok"] is False and out["error"] == "repo not found"


def test_an_empty_mcp_answer_is_flagged():
    out = T.dispatch(_mcp_client(None), "call_registered_mcp_tool", {"server": "s", "tool": "t", "confirm": True})
    assert out["ok"] is True and "returned nothing" in out["note"]


# -- schedules --------------------------------------------------------------
def _sched_client(audits, changed):
    audits = list(audits)
    return SimpleNamespace(schedules=SimpleNamespace(
        audit=lambda name, run_limit=10: audits.pop(0) if len(audits) > 1 else audits[0],
        get=lambda name, typed=False: {"date_changed": changed},
    ))


NOT_FIRED = {"enabled": True, "ever_fired": False, "runs": [], "problems": ["schedule has never fired"]}


def test_pyfsrs_pending_verdict_gets_a_wait_hint():
    pending = {**NOT_FIRED, "problems": [], "pending": True, "first_fire_due": "2026-03-02T11:02:00+00:00"}
    out = T.dispatch(_sched_client([pending], "x"), "diagnose_schedule", {"name": "hb"})
    assert "2026-03-02T11:02" in out["note"] and "wait_for_fire=true" in out["note"]


def test_a_schedule_that_should_have_fired_keeps_its_problem():
    out = T.dispatch(_sched_client([NOT_FIRED], "x"), "diagnose_schedule", {"name": "hb"})
    assert out["problems"] and "note" not in out


def test_wait_for_fire_polls_until_it_fires(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    fired = {**NOT_FIRED, "ever_fired": True, "problems": []}
    out = T.dispatch(_sched_client([NOT_FIRED, NOT_FIRED, fired], "x"), "diagnose_schedule",
                     {"name": "hb", "wait_for_fire": True})
    assert out["ever_fired"] is True


# -- connectors -------------------------------------------------------------
def _bundle(tmp_path, version="1.0.0"):
    path = tmp_path / "c.tgz"
    data = json.dumps({"name": "dadjoke", "version": version}).encode()
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("dadjoke/info.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return str(path)


def _conn_client(bumped=None):
    calls = []
    conns = SimpleNamespace(
        install_from_file=lambda path, **k: calls.append(("install", k)) or {"name": "dadjoke", **(
            {"bumped": bumped} if bumped else {})},
        republish=lambda name: calls.append(("republish", name)) or {"ok": True},
        healthcheck=lambda name: {"status": "Available"},
    )
    return SimpleNamespace(connectors=conns), calls


def test_install_defaults_to_pyfsr_bumping_a_same_version_bundle(tmp_path):
    c, calls = _conn_client(bumped="1.0.1")
    out = T.dispatch(c, "install_connector", {"path": _bundle(tmp_path), "confirm": True})
    assert calls[0][1]["on_existing_version"] == "bump_patch"
    assert out["installed_version"] == "1.0.1" and "1.0.1" in out["note"]


def test_allow_reinstalls_in_place_then_recycles_the_workers(tmp_path):
    c, calls = _conn_client()
    T.dispatch(c, "install_connector",
               {"path": _bundle(tmp_path), "on_existing_version": "allow", "confirm": True})
    assert calls == [("install", calls[0][1]), ("republish", "dadjoke")]


# -- errors -----------------------------------------------------------------
def _http_error(status, url="/api/3/alerts"):
    exc = FortiSOARException("boom")
    exc.response = SimpleNamespace(status_code=status, request=None, url=url, text="")
    return exc


@pytest.mark.parametrize(("status", "phrase", "retry"), [
    (404, "instead of guessing", None), (403, "survey_box", None), (503, "retry once", 30)])
def test_errors_carry_a_next_action(status, phrase, retry):
    err = T._agent_error(_http_error(status), tool="t")["error"]
    assert phrase in err["next_action"] and err.get("retry_after_seconds") == retry


def test_an_auth_overload_says_back_off():
    err = T._agent_error(_http_error(400, "/api/auth/authenticate"), tool="t")["error"]
    assert err["retry_after_seconds"] == 60 and "do not retry in a loop" in err["next_action"]


# -- playbook YAML: fsr_playbooks owns the run-time traps ------------------
def test_compile_surfaces_fsr_playbooks_snippet_findings():
    yml = """collection: C
playbooks:
  - name: P
    steps:
      - name: Start
        type: start
        module: alerts
        next: S
      - name: S
        type: code_snippet
        code: |
            print(all([1]))
"""
    out = T.dispatch(None, "compile_playbook_yaml", {"yaml_text": yml})
    assert out["ok"] is False
    assert "'all'" in json.dumps(out["errors"])

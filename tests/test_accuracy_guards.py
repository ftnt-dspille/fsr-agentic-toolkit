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
from fsr_agentic_toolkit.workflows.yaml_lint import lint_yaml

FIELDS = [{"name": "name", "type": "string"}, {"name": "status", "type": "picklists"},
          {"name": "dueDate", "type": "datetime"}]


class _Records:
    def __init__(self, store):
        self.store = store
        self.queried = None

    def query(self, body):
        self.queried = body
        return {"hydra:member": []}

    def create(self, data, **_):
        rec = {"@id": "/api/3/alerts/u1", "uuid": "u1", **data}
        # The box keeps only the fields it knows.
        self.store["u1"] = {k: v for k, v in rec.items() if k in {"@id", "uuid", "name", "status", "dueDate"}}
        return rec

    def get(self, ref):
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


# -- queries ----------------------------------------------------------------
def test_a_misspelt_filter_field_is_refused_not_widened():
    c = _Client()
    out = T.dispatch(c, "query_records", {"module": "alerts",
                                          "filters": [{"field": "statuss", "operator": "eq", "value": "x"}]})
    assert "no field 'statuss'" in out["error"]["message"]
    assert "status" in out["error"]["message"]  # the close match is offered
    assert c.recs.queried is None  # never sent: the API would have returned everything


def test_a_filter_without_an_operator_is_refused():
    out = T.dispatch(_Client(), "query_records", {"module": "alerts", "filters": [{"field": "name"}]})
    assert "needs both 'field' and 'operator'" in out["error"]["message"]


def test_a_dotted_field_is_checked_by_its_root():
    c = _Client()
    T.dispatch(c, "query_records", {"module": "alerts",
                                    "filters": [{"field": "status.itemValue", "operator": "eq", "value": "Open"}]})
    assert c.recs.queried is not None


# -- writes -----------------------------------------------------------------
def test_an_iso_date_is_stored_as_epoch_seconds():
    c = _Client()
    out = T.dispatch(c, "create_record", {"module": "alerts",
                                          "data": {"name": "a", "dueDate": "2026-01-02T03:04:05Z"}})
    want = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).timestamp())
    assert c.store["u1"]["dueDate"] == want
    assert out["converted_dates"] == ["dueDate"]


def test_a_write_is_read_back_and_a_dropped_field_reported():
    c = _Client()
    out = T.dispatch(c, "create_record", {"module": "alerts", "data": {"name": "a", "nosuchfield": 1}})
    assert out["readback"]["verified"] is False
    assert "nosuchfield" in out["readback"]["mismatches"]


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
        return {"verdict": "benign"}


def test_wait_blocks_until_the_investigation_finishes(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = SimpleNamespace(ai=_AI(["pending", "inprogress", "completed"]))
    out = T.dispatch(c, "run_fortiai_investigation", {"ref": "/api/3/alerts/u1", "wait": True})
    assert out["investigation_task_id"] == "inv-1"
    assert out["status"] == "completed" and out["result"] == {"verdict": "benign"}


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


def test_a_new_schedule_is_pending_not_broken():
    c = _sched_client([NOT_FIRED], datetime.now(UTC).isoformat())
    out = T.dispatch(c, "diagnose_schedule", {"name": "hb"})
    assert out["pending"] is True and out["problems"] == []
    assert "wait_for_fire=true" in out["note"]


def test_an_old_schedule_that_never_fired_is_still_a_problem():
    c = _sched_client([NOT_FIRED], "2020-01-01T00:00:00Z")
    out = T.dispatch(c, "diagnose_schedule", {"name": "hb"})
    assert out["problems"] and "pending" not in out


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


def _conn_client(installed):
    calls = []
    conns = SimpleNamespace(
        resolve_version=lambda name: installed,
        install_from_file=lambda path, **k: calls.append(k) or {"name": "dadjoke"},
        healthcheck=lambda name: {"status": "Available"},
        clear_cache=lambda: None,
    )
    return SimpleNamespace(connectors=conns), calls


def test_a_same_version_reinstall_is_refused(tmp_path):
    c, calls = _conn_client("1.0.0")
    out = T.dispatch(c, "install_connector", {"path": _bundle(tmp_path), "confirm": True})
    assert "Bump the version" in out["error"]["message"] and not calls


def test_an_allowed_same_version_reinstall_warns_about_stale_workers(tmp_path):
    c, calls = _conn_client("1.0.0")
    out = T.dispatch(c, "install_connector",
                     {"path": _bundle(tmp_path), "allow_same_version": True, "confirm": True})
    assert calls[0]["replace"] is True
    assert out["retry_after_seconds"] and "previous code" in out["warnings"][0]


def test_a_new_version_installs_and_reports_the_settled_version(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c, calls = _conn_client("1.0.0")
    c.connectors.resolve_version = lambda name: "1.1.0" if calls else "1.0.0"
    out = T.dispatch(c, "install_connector", {"path": _bundle(tmp_path, "1.1.0"), "confirm": True})
    assert out["installed_version"] == "1.1.0" and "warnings" not in out


def test_an_orphaned_config_field_and_hardcoded_host_are_flagged(tmp_path):
    (tmp_path / "info.json").write_text(json.dumps({"configuration": {"fields": [{"name": "api_key"}]}}))
    (tmp_path / "connector.py").write_text("import requests\nrequests.get('https://x.example.com')\n")
    codes = {w["code"] for w in T._config_field_warnings(tmp_path)}
    assert codes == {"config-field-unused", "config-no-server"}


def test_a_used_server_field_is_clean(tmp_path):
    (tmp_path / "info.json").write_text(json.dumps({"configuration": {"fields": [{"name": "server"}]}}))
    (tmp_path / "connector.py").write_text("import requests\nrequests.get(config.get('server'))\n")
    assert T._config_field_warnings(tmp_path) == []


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


# -- playbook YAML runtime traps --------------------------------------------
def _pb(*steps):
    import yaml
    return yaml.safe_dump({"playbooks": [{"name": "p", "steps": list(steps)}]})


def test_a_snippet_that_never_prints_is_flagged():
    codes = [i["code"] for i in lint_yaml(_pb({"name": "S", "type": "code_snippet", "code": "x = 1\n"}))]
    assert codes == ["snippet-no-print"]


def test_a_blocked_name_is_flagged_but_not_inside_a_string():
    bad = lint_yaml(_pb({"name": "S", "type": "code_snippet", "code": "seq = [1]\nprint(seq)\n"}))
    ok = lint_yaml(_pb({"name": "S", "type": "code_snippet", "code": "print('not set')\n"}))
    assert [i["code"] for i in bad] == ["snippet-blocked-name"] and ok == []


def test_hydra_member_on_a_find_record_result_is_flagged():
    codes = [i["code"] for i in lint_yaml(_pb(
        {"name": "Find Alerts", "type": "find_record", "module": "alerts"},
        {"name": "Use", "type": "set_variable",
         "variables": {"rows": "{{ vars.steps.Find_Alerts['hydra:member'] }}"}}))]
    assert codes == ["find-record-hydra"]


def test_a_literal_picklist_iri_in_a_write_is_flagged():
    iri = "/api/3/picklists/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12
    codes = [i["code"] for i in lint_yaml(_pb(
        {"name": "W", "type": "update_record", "module": "alerts", "fields": {"status": iri}}))]
    assert codes == ["picklist-literal-iri"]


def test_compile_gate_fails_on_a_runtime_trap():
    yml = """collection: C
playbooks:
  - name: P
    steps:
      - name: Start
        type: start
        next: S
      - name: S
        type: code_snippet
        code: |
          x = 1
"""
    out = T.dispatch(None, "compile_playbook_yaml", {"yaml_text": yml})
    if "error" in out:
        pytest.skip("fsr_playbooks compiler unavailable")
    assert out["ok"] is False and out["runtime_traps"][0]["code"] == "snippet-no-print"

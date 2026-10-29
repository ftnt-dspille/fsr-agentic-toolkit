"""run_playbook_yaml: gate -> publish -> trigger -> wait -> read, in one call.

No box: the compile gate, the publish and the run are fakes shaped like the
recorded live responses. The point is the ordering and the stop conditions: a
failed gate deploys nothing, a failed run carries its diagnosis, and cleanup
removes only what this call created.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fsr_agentic_toolkit.verbs import tools as T
from fsr_agentic_toolkit.workflows import MemoryResultStore, set_result_store
from fsr_agentic_toolkit.workflows import run as R

YAML = "collection: c\nplaybooks: []\n"


@pytest.fixture(autouse=True)
def _store():
    set_result_store(MemoryResultStore())


class FakePlaybooks:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.deleted, self.calls = result, exc, [], []

    def run_and_wait(self, name=None, **kw):
        self.calls.append((name or kw.get("playbook_uuid", "").removeprefix("uuid-"), kw))
        if self.exc:
            raise self.exc
        return self.result

    def delete(self, uuid):
        self.deleted.append(uuid)


class FakeCollections:
    def __init__(self, workflows=()):
        self.deleted = []
        self.workflows = list(workflows)

    def get(self, uuid):
        return {"workflows": self.workflows}

    def delete(self, uuid):
        self.deleted.append(uuid)


def _run(status="finished", failure=None):
    steps = [SimpleNamespace(name="Capture", status="finished", duration_ms=12,
                             result_preview='{"probe": 1}')]
    return SimpleNamespace(pk="9", task_id="t", status=status, steps=steps, failure=failure,
                           succeeded=status == "finished")


def _wire(monkeypatch, *, gate=None, report=None, playbooks=None, publish_exc=None):
    gate = gate or {"ok": True, "errors": [], "collection": "c", "playbooks": ["pb"]}
    report = report or {"new": ["pb"], "changed": [], "unchanged": [], "created": ["u1"],
                        "collection_uuid": "cu"}
    client = SimpleNamespace(playbooks=playbooks or FakePlaybooks(_run()),
                             workflow_collections=FakeCollections())
    published = []

    def pub(_c, **kw):
        published.append(kw)
        if publish_exc:
            raise publish_exc
        return {"ok": True, "collection": "c", "playbooks": ["pb"], "result": report}

    monkeypatch.setattr(T, "_h_compile_playbook_yaml", lambda _c, **kw: gate)
    monkeypatch.setattr(R, "_uuid_of", lambda _y, name: f"uuid-{name}")
    monkeypatch.setattr(R, "_silent_empties", lambda *_a: [])
    monkeypatch.setattr(T, "_h_publish_playbook", pub)
    monkeypatch.setattr(R._shared, "_live_client", lambda: client)
    return client, published


def test_happy_path_runs_and_reads_back_every_step(monkeypatch):
    client, published = _wire(monkeypatch)
    r = R.run_playbook_yaml(YAML)
    assert r.status == "answered" and len(published) == 1
    assert client.playbooks.calls[0][0] == "pb"
    assert r.data["run"]["status"] == "finished"
    assert r.data["run"]["steps"][0]["name"] == "Capture"
    assert any("ran to completion" in f.claim for f in r.findings)
    assert not client.playbooks.deleted  # no cleanup unless asked


def test_failed_gate_deploys_nothing(monkeypatch):
    client, published = _wire(monkeypatch, gate={
        "ok": False, "errors": [{"message": "unknown step type", "severity": "error"}],
        "playbooks": ["pb"]})
    r = R.run_playbook_yaml(YAML)
    assert r.status == "blocked" and not published and not client.playbooks.calls
    assert any("unknown step type" in f.claim for f in r.findings)


def test_gate_warnings_are_unknowns_not_blockers(monkeypatch):
    _wire(monkeypatch, gate={"ok": True, "playbooks": ["pb"], "collection": "c",
                             "errors": [{"message": "bare name 'now'", "severity": "warning"}]})
    r = R.run_playbook_yaml(YAML)
    assert any("bare name 'now'" in u for u in r.unknowns)


def test_several_playbooks_need_a_name(monkeypatch):
    client, published = _wire(monkeypatch, gate={"ok": True, "errors": [], "collection": "c",
                                                 "playbooks": ["a", "b"]})
    r = R.run_playbook_yaml(YAML)
    assert r.status == "blocked" and not published
    r2 = R.run_playbook_yaml(YAML, playbook="b")
    assert client.playbooks.calls[0][0] == "b" and r2.status == "answered"


def test_publish_failure_is_blocked_not_raised(monkeypatch):
    _, _ = _wire(monkeypatch, publish_exc=RuntimeError("live steps differ"))
    r = R.run_playbook_yaml(YAML)
    assert r.status == "blocked" and any("live steps differ" in u for u in r.unknowns)


def test_failed_run_carries_a_diagnosis(monkeypatch):
    fail = SimpleNamespace(failing_step="Capture", error_message="boom")
    _wire(monkeypatch, playbooks=FakePlaybooks(_run("failed", fail)))
    seen = {}

    def fake_debug(target, yaml_text=None):
        seen["target"] = target
        return SimpleNamespace(id="debug-1", findings=[SimpleNamespace(claim="root cause")])

    monkeypatch.setattr(R, "debug_run", fake_debug)
    r = R.run_playbook_yaml(YAML)
    assert r.status == "partial" and seen["target"] == "9"
    assert r.data["debug_id"] == "debug-1"
    assert any("failed" in f.claim and "Capture" in f.claim for f in r.findings)
    assert r.next[0].action.startswith("repair_playbook(follow_up='debug-1'")


def test_timeout_is_partial_and_says_how_to_continue(monkeypatch):
    _wire(monkeypatch, playbooks=FakePlaybooks(exc=TimeoutError()))
    r = R.run_playbook_yaml(YAML, timeout=1)
    assert r.status == "partial"
    assert any(n.action.startswith("wait_for_playbook_run") for n in r.next)


def test_cleanup_removes_only_what_this_call_created(monkeypatch):
    client, _ = _wire(monkeypatch)
    R.run_playbook_yaml(YAML, cleanup=True)
    assert client.playbooks.deleted == ["u1"]
    assert client.workflow_collections.deleted == ["cu"]


def test_cleanup_leaves_a_preexisting_collection_alone(monkeypatch):
    client, _ = _wire(monkeypatch, report={"new": ["pb"], "changed": ["old"], "unchanged": [],
                                           "created": ["u1"], "collection_uuid": "cu"})
    r = R.run_playbook_yaml(YAML, cleanup=True)
    assert not client.playbooks.deleted and not client.workflow_collections.deleted
    assert any("cleanup skipped" in u for u in r.unknowns)


def test_cleanup_keeps_a_collection_that_still_holds_other_playbooks(monkeypatch):
    client, _ = _wire(monkeypatch)
    client.workflow_collections.workflows = ["someone-elses"]
    r = R.run_playbook_yaml(YAML, cleanup=True)
    assert client.playbooks.deleted == ["u1"] and not client.workflow_collections.deleted
    assert any("collection was kept" in u for u in r.unknowns)


def test_spec_is_a_tier2_write_not_destructive():
    spec = T.REGISTRY["run_playbook_yaml"]
    assert spec.workflow and spec.mutating and not spec.destructive


def test_gate_failure_carries_the_compilers_suggestion_and_path(monkeypatch):
    _wire(monkeypatch, gate={"ok": False, "playbooks": ["pb"], "errors": [{
        "message": "unknown connector: 'virus-total'", "severity": "error",
        "path": "pb.steps.s1.arguments.connector", "suggestion": "did you mean 'virustotal'?"}]})
    r = R.run_playbook_yaml(YAML)
    claim = next(f.claim for f in r.findings if "virus-total" in f.claim)
    assert "did you mean 'virustotal'?" in claim and "pb.steps.s1.arguments.connector" in claim


def test_timeout_names_the_pending_prompt_and_keeps_the_playbook(monkeypatch):
    client, _ = _wire(monkeypatch, playbooks=FakePlaybooks(exc=TimeoutError()))
    yaml_text = ("collection: c\nplaybooks:\n  - name: pb\n    steps:\n"
                 "      - {name: Ask, type: manual_input, title: 'Go?'}\n")
    r = R.run_playbook_yaml(yaml_text, cleanup=True, timeout=1)
    assert any("Go?" in u for u in r.unknowns)
    assert any("answers={'Go?'" in n.action for n in r.next)
    # deleting the playbook would orphan the run that is still waiting
    assert not client.playbooks.deleted and not client.workflow_collections.deleted
    assert any("cleanup skipped" in u for u in r.unknowns)


def test_a_green_run_with_empty_arguments_is_partial_not_answered(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(R, "_silent_empties", lambda *_a: ["step 'S1' argument 'a' rendered empty"])
    r = R.run_playbook_yaml(YAML)
    assert r.status == "partial" and any("may have done nothing" in u for u in r.unknowns)
    assert any("record_uuid" in n.why for n in r.next)


class _Env:
    def __init__(self, env):
        self.env = env


def test_silent_empties_reads_the_runs_own_env():
    yaml_text = ("playbooks:\n  - name: pb\n    steps:\n"
                 "      - {name: S1, type: set_variable, vars: {a: '{{ vars.input.records[0].name }}',"
                 " b: '{{ x }}', c: literal}}\n")
    client = SimpleNamespace(playbooks=SimpleNamespace(
        run_env=lambda pk: _Env({"a": "", "c": "literal"})))
    out = R._silent_empties(client, "1", yaml_text)
    assert len(out) == 2
    assert "variable 'a' is empty" in out[0] and "variable 'b' was never set" in out[1]
    # a literal that is set, and a run with no pk, report nothing
    assert R._silent_empties(client, None, yaml_text) == []


def test_unknown_connector_points_at_the_installed_list(monkeypatch):
    _wire(monkeypatch, gate={"ok": False, "playbooks": ["pb"], "errors": [{
        "code": "ErrorCode.UNKNOWN_CONNECTOR", "message": "unknown connector: 'x'"}]})
    r = R.run_playbook_yaml(YAML)
    assert any(n.action == "list_connectors()" for n in r.next)


def test_a_playbook_with_no_trigger_says_a_manual_start_was_added(monkeypatch):
    _wire(monkeypatch)
    yaml_text = "collection: c\nplaybooks:\n  - name: pb\n    steps:\n      - {name: S1, type: end}\n"
    r = R.run_playbook_yaml(yaml_text)
    assert any("no trigger step" in u and "manual 'Start'" in u for u in r.unknowns)
    with_trigger = yaml_text.replace("{name: S1, type: end}", "{name: Go, type: start, next: E}")
    r2 = R.run_playbook_yaml(with_trigger)
    assert not any("no trigger step" in u for u in r2.unknowns)


def test_return_in_a_snippet_gets_the_print_fix():
    class E:
        code, path, severity = "ErrorCode.SNIPPET", "p", "error"
        message = "code-snippet body is not valid Python: 'return' outside function (line 2)"
        suggestion = "use a bare expression/assignment or wrap it in a function you then call"
    row = T._compile_errors(SimpleNamespace(errors=[E()]))[0]
    assert "print(<value>)" in row["suggestion"] and "bare expression" not in row["suggestion"]

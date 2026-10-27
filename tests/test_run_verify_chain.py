"""The author -> run -> read -> verify chain, end to end, offline.

The run/debug/goal-eval survey found no test -- mocked or live -- that chains
the run tools together: each is unit-tested against fakes in isolation, so a
shape mismatch between them (a run whose pk `diagnose_run` cannot read, a
created record `verify_effect` cannot find) would pass every existing test.

This drives the real MCP dispatch + handlers in sequence against one stateful
fake that plays the box backend: creating a record stores it AND enqueues the
run its trigger would start, so `wait_for_playbook_run` and `diagnose_run` read
what `create_record` set in motion, and `verify_effect` reads the record back.
No box, no LLM. It asserts the OUTPUTS compose, which the isolated tests cannot.
"""
from __future__ import annotations

from typing import Any

from fsr_playbooks.mcp_server import _shared
from pyfsr.api.playbooks import PlaybooksAPI
from pyfsr.records import RecordSet

from fsr_agentic_toolkit.verbs import tools
from fsr_agentic_toolkit.workflows.verify import verify_effect

_PB_UUID = "wf-1"
_PB_NAME = "Create Alert On Demand"


def _run(pk: str, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"@id": f"/api/wf/api/workflows/{pk}/", "name": _PB_NAME,
            "status": status, "env": {}, "steps": steps}


class ChainBox:
    """One fake FortiSOAR for the whole chain.

    Creating a record (POST /api/3/<module>) stores it with a uuid and enqueues
    a finished run of the playbook -- exactly what a record-create trigger does
    on a live box -- so the downstream run tools have something true to read.
    """

    base_url = ""

    def __init__(self) -> None:
        self.definition = {
            "uuid": _PB_UUID, "name": _PB_NAME,
            "steps": [{"name": "Start", "stepType": {"name": "cybersponse.abstract_trigger"}},
                      {"name": "Make Alert", "stepType": {"name": "cybersponse.create"}}],
        }
        self.store: dict[str, dict[str, Any]] = {}            # uuid -> record
        self.runs: list[dict[str, Any]] = []
        self.run_by_pk: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self.playbooks = PlaybooksAPI(self)

    # the real client exposes records(module); handlers call client.records(...)
    def get(self, endpoint: str, params: Any = None, **kw: Any) -> dict[str, Any]:
        if endpoint.startswith("/api/3/workflows/") and "?" not in endpoint:
            return self.definition
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            return {"hydra:member": []}
        if endpoint.startswith("/api/wf/api/workflows/"):
            tail = endpoint.split("/api/wf/api/workflows/", 1)[1]
            if tail.startswith("?"):
                return {"hydra:member": self.runs}
            return self.run_by_pk.get(tail.split("/")[0], {})
        if endpoint.startswith("/api/3/"):  # a record read: /api/3/<module>/<uuid>
            uuid = endpoint.rstrip("/").rsplit("/", 1)[-1]
            return self.store.get(uuid, {})
        return {}

    def post(self, endpoint: str, data: Any = None, params: Any = None, **kw: Any) -> dict[str, Any]:
        # a record create: store it, then enqueue the run its trigger starts.
        if endpoint.startswith("/api/3/") and endpoint.count("/") == 3:
            self._seq += 1
            uuid = f"rec-{self._seq}"
            rec = {"uuid": uuid, "@id": f"{endpoint}/{uuid}", **(data or {})}
            self.store[uuid] = rec
            pk = f"100{self._seq}"
            run = _run(pk, "finished",
                       [{"name": "Start", "status": "finished"},
                        {"name": "Make Alert", "status": "finished",
                         "result": {"uuid": uuid}}])
            self.runs.insert(0, run)
            self.run_by_pk[pk] = run
            return rec
        return {}


# client.records(module) is a method on the real client; add it to the fake.
def _records(self, module):
    return RecordSet(self, module)


ChainBox.records = _records  # type: ignore[assignment]


def test_create_triggers_run_that_wait_and_diagnose_then_verify_all_read(monkeypatch):
    box = ChainBox()

    # 1. create a record (the trigger). resolve_picklists off: no picklist box calls.
    created = tools.dispatch(box, "create_record", {
        "module": "alerts",
        "data": {"name": "Demo alert", "severity": "High"},
        "resolve_picklists": False, "strict_picklists": False})
    # the write result carries the new record (uuid round-trips downstream)
    rec = created.get("record", created)
    uuid = rec.get("uuid") or created.get("uuid")
    assert uuid, created

    # 2. wait for the run the create set in motion -> terminal, and it is OURS
    run = tools.dispatch(box, "wait_for_playbook_run",
                         {"playbook_uuid": _PB_UUID, "timeout": 5, "poll_interval": 0})
    assert run.get("status") == "finished", run

    # 3. diagnose that same run -> completed, both defined steps reached. This is
    #    the compose point the isolated tests never exercise: the run shape from
    #    wait_for_playbook_run must be readable by diagnose_run.
    diag = tools.dispatch(box, "diagnose_run", {"playbook_uuid": _PB_UUID})
    assert diag["verdict"] == "completed", diag
    assert diag["not_reached"] == [], diag

    # 4. verify the goal effect on the record the chain created.
    monkeypatch.setattr(_shared, "_live_client", lambda: box)
    monkeypatch.setattr(
        _shared, "live_request_with_reauth",
        lambda m, c, url, **k: type("R", (), {
            "status_code": 200,
            "json": lambda self, _u=url: box.store.get(_u.rstrip("/").rsplit("/", 1)[-1], {})})())
    res = verify_effect("alerts", uuid, {"name": "Demo alert"})
    assert res.status == "answered", (res.status, getattr(res, "unknowns", None))

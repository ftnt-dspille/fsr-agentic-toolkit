"""Unit tests for PlaybooksAPI.diagnose_run + the diagnose_run MCP tool.

diagnose_run diffs a playbook's DEFINITION (step graph) against a RUN (executed
step statuses) so an agent can answer "did my playbook run do what I defined?"
without cross-referencing get_definition / run_env / why_failed by hand.
"""

from __future__ import annotations

from typing import Any

from pyfsr.api.playbooks import PlaybooksAPI

from fsr_agentic_toolkit.verbs import tools


def _definition(uuid: str, name: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A /api/3/workflows/<uuid> definition payload with inlined steps."""
    return {"uuid": uuid, "name": name, "steps": steps}


def _step(name: str, type_name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "stepType": {"name": type_name}, "arguments": args}


def _run(pk: str, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A /api/wf/api/workflows/<pk>/ run payload with per-step results."""
    return {
        "@id": f"/api/wf/api/workflows/{pk}/",
        "name": "PB",
        "status": status,
        "env": {},
        "steps": steps,
    }


def _run_step(name: str, status: str, result: Any = None) -> dict[str, Any]:
    return {"name": name, "status": status, "result": result}


class DiagnoseClient:
    """Routes the four endpoints diagnose_run touches:

    - GET /api/3/workflows/<uuid>            -> playbook definition (with steps)
    - GET /api/wf/api/workflows/?...         -> live run list (latest run)
    - GET /api/wf/api/historical-workflows/? -> historical run list (empty here)
    - GET /api/wf/api/workflows/<pk>/...     -> one run with step_detail
    """

    def __init__(
        self,
        *,
        definition: dict[str, Any] | None = None,
        runs: list[dict[str, Any]] | None = None,
        run_by_pk: dict[str, dict[str, Any]] | None = None,
    ):
        self.definition = definition
        self.runs = runs or []
        self.run_by_pk = run_by_pk or {}
        self.get_calls: list[tuple[str, Any]] = []
        # The MCP handlers reach the API through client.playbooks (like FortiSOAR).
        self.playbooks = PlaybooksAPI(self)

    def get(self, endpoint: str, params: Any = None, **kw: Any) -> dict[str, Any]:
        self.get_calls.append((endpoint, params))
        # Definition fetch: /api/3/workflows/<uuid>
        if endpoint.startswith("/api/3/workflows/") and "?" not in endpoint.split("/api/3/workflows/", 1)[1]:
            return self.definition or {}
        # Single run fetch: /api/wf/api/workflows/<pk>/?format=json&step_detail=true
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            tail = endpoint.split("/api/wf/api/historical-workflows/", 1)[1]
            if tail.startswith("?"):
                return {"hydra:member": []}
            return {}
        if endpoint.startswith("/api/wf/api/workflows/"):
            tail = endpoint.split("/api/wf/api/workflows/", 1)[1]
            if tail.startswith("?"):  # list
                return {"hydra:member": self.runs}
            # single run
            pk = tail.split("/")[0]
            return self.run_by_pk.get(pk, {})
        return {}

    def post(self, endpoint: str, data: Any = None, params: Any = None, **kw: Any) -> dict[str, Any]:
        return {}


# --- happy path: run completed, all defined steps reached -------------------


# --- failed mid-run: a defined step never reached --------------------------


# --- no run: playbook found but never executed -----------------------------


# --- drift: run recorded a step the current definition lacks ---------------


# --- explicit run arg skips the latest-run lookup --------------------------


# --- no definition: unresolvable playbook ----------------------------------


# --- MCP dispatch surfaces the structured diff -----------------------------
def test_dispatch_diagnose_run():
    client = DiagnoseClient(
        definition=_definition(
            "u1",
            "Block IP",
            [_step("Start", "cybersponse.abstract_trigger"), _step("Block", "Connectors")],
        ),
        runs=[_run("1042", "failed", [])],
        run_by_pk={
            "1042": _run(
                "1042",
                "failed",
                [
                    _run_step("Start", "finished"),
                    _run_step("Block", "failed", result={"Error message": "boom"}),
                ],
            )
        },
    )
    out = tools.dispatch(client, "diagnose_playbook_run", {"playbook_uuid": "u1"})
    assert out["verdict"] == "failed"
    assert out["failing_step"] == "Block"
    assert out["error_message"] == "boom"
    assert out["not_reached"] == []
    # The tool is registered + documented for an agent.
    spec = {t["name"]: t for t in tools.tool_schemas()}
    assert "diagnose_playbook_run" in spec
    assert "verdict" in (spec["diagnose_playbook_run"]["description"] or "")

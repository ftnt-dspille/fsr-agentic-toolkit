"""T2 workflows against a fake box: orient, provision_module (the three breaks),
repair_playbook, investigate_alert, build_playbook, and the heists use-case chain.

The fake box answers the records paths the way 8.0 does for each break: an
unpublished module 404s "No route found", a role without the module 403s
"Access Denied", a team-scoped record 403s naming the team.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fsr_playbooks.mcp_server import _shared

from fsr_agentic_toolkit.workflows import MemoryResultStore, set_result_store
from fsr_agentic_toolkit.workflows import debug as D
from fsr_agentic_toolkit.workflows import orient as O
from fsr_agentic_toolkit.workflows.build import build_playbook
from fsr_agentic_toolkit.workflows.investigate import from_verdict, investigate_alert
from fsr_agentic_toolkit.workflows.orient import orient
from fsr_agentic_toolkit.workflows.provision import provision_module
from fsr_agentic_toolkit.workflows.repair import repair_playbook
from fsr_agentic_toolkit.workflows.verify import verify_effect

HEIST_YAML = (Path(__file__).parent / "fixtures" / "workflows" / "heist_intake.yaml").read_text()
HEIST_FIELDS = [{"name": n} for n in ("name", "codename", "takeUsd", "crew", "status")]


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body


class _Pending:
    def __init__(self, module):
        self.module = module


class FakeBox:
    base_url = ""

    def __init__(self):
        self.modules: dict[str, dict] = {}
        self.other_staged: list[str] = []
        self.published_calls = 0
        self.busy: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.modules_admin = self._Admin(self)
        self.roles = self._Roles(self)

    def add(self, name, *, published=True, role_ok=True, team_block=False, fields=None,
            records=None):
        self.modules[name] = {"published": published, "role_ok": role_ok,
                              "team_block": team_block,
                              "fields": {f["name"]: "text" for f in (fields or [{"name": "name"}])},
                              "records": dict(records or {})}

    # -- REST -----------------------------------------------------------------
    def request(self, method, client, url, **kw):
        path, _, query = url.partition("?")
        if path == "/api/3/model_metadatas":
            name = dict(p.split("=", 1) for p in query.split("&")).get("type")
            m = self.modules.get(name)
            if not m:
                return _Resp(200, {"hydra:member": []})
            return _Resp(200, {"hydra:member": [{"attributes": [
                {"name": k, "type": v} for k, v in m["fields"].items()]}]})
        parts = [p for p in path.split("/") if p]  # api, 3, module[, uuid]
        if method == "delete":
            return _Resp(204)
        name = parts[2]
        m = self.modules.get(name)
        if not m or not m["published"]:
            return _Resp(404, text=f'No route found for "GET /api/3/{name}"')
        if not m["role_ok"]:
            return _Resp(403, text="Access Denied.")
        if m["team_block"]:
            return _Resp(403, text="record is owned by a team the user is not on")
        if method == "post":
            self.writes.append(("post", name))
            return _Resp(201, {"@id": f"/api/3/{name}/probe-1"})
        if len(parts) == 4:
            rec = m["records"].get(parts[3])
            return _Resp(200, rec) if rec is not None else _Resp(404, text="not found")
        return _Resp(200, {"hydra:member": []})

    class _Admin:
        def __init__(self, box):
            self.box = box

        def is_published(self, module):
            return bool(self.box.modules.get(module, {}).get("published"))

        def get_staging(self, module):
            return self.box.modules.get(module)

        def create_module(self, module, *, label=None, plural=None, fields=None):
            self.box.add(module, published=False, role_ok=False, fields=fields)

        def pending_changes(self):
            if self.box.busy:  # a publish already in flight: the box answers 503 states
                from types import SimpleNamespace

                from pyfsr.exceptions import FortiSOARException
                raise FortiSOARException(self.box.busy.pop(0), SimpleNamespace(status_code=503))
            staged = [n for n, m in self.box.modules.items() if not m["published"]]
            return [_Pending(n) for n in staged + self.box.other_staged]

        def publish(self, progress=None):
            self.box.published_calls += 1
            for m in self.box.modules.values():
                m["published"] = True

    class _Roles:
        def __init__(self, box):
            self.box = box
            self.grants: list[tuple[str, str]] = []

        def grant_module_permissions(self, role, *, module, can_execute=True):
            self.grants.append((role, module))
            self.box.modules[module]["role_ok"] = True


@pytest.fixture
def box(monkeypatch):
    set_result_store(MemoryResultStore())
    b = FakeBox()
    monkeypatch.setattr(_shared, "_live_client", lambda: b)
    monkeypatch.setattr(_shared, "live_request_with_reauth", b.request)
    monkeypatch.setattr(O, "_connectors", lambda goal: {"connectors": [
        {"name": "virustotal", "status": "Available"}]})
    from fsr_playbooks.mcp_server import tools_verify
    monkeypatch.setattr(tools_verify, "_LIVE_MODULES", {})
    return b


# --- provision_module grader: each seeded break is named with its fix -----------

@pytest.mark.parametrize(("state", "expect", "fix_word"), [
    ({"published": False}, "unpublished", "publish"),
    ({"role_ok": False}, "role", "grant"),
    ({"team_block": True}, "team", "team"),
    ({}, "ok", None),
])
def test_check_only_names_the_break_and_its_fix(box, state, expect, fix_word):
    box.add("agentlab_assets", **state)
    r = provision_module("agentlab_assets", check_only=True)
    assert r.data["access"] == expect
    if fix_word:
        claim = r.findings[0].claim
        assert expect in claim and fix_word in claim.lower()
        assert r.next and r.next[0].tier == 3
    assert box.writes == [] and box.published_calls == 0  # check_only writes nothing


def test_provision_creates_publishes_grants_and_proves_access(box):
    r = provision_module("heists", label="Heist", fields=HEIST_FIELDS)
    assert r.status == "answered", r.findings
    claims = " | ".join(f.claim for f in r.findings)
    for part in ("created heists", "published heists", "granted 'Full App Permissions'",
                 "can read heists", "can create heists"):
        assert part in claims
    assert box.published_calls == 1 and box.roles.grants == [("Full App Permissions", "heists")]


def test_provision_waits_out_a_publish_in_flight_and_reports_it(box, monkeypatch):
    """Live: the box answered with 503 progress states and they came back as
    errors. provision_module waits them out and reports them as progress."""
    from fsr_agentic_toolkit.workflows import provision as P
    monkeypatch.setattr(P, "IDLE_INTERVAL", 0)
    box.busy = ["Generating Models", "Generating Models", "Clearing Cache",
                "Cleaning Up Old Backups", "Encrypt Database"]
    r = provision_module("heists", label="Heist", fields=HEIST_FIELDS)
    assert r.status == "answered", r.findings
    waited = [f.claim for f in r.findings if f.claim.startswith("waited out")]
    assert waited == [("waited out a publish already in progress: Generating Models -> "
                      "Clearing Cache -> Cleaning Up Old Backups -> Encrypt Database")]
    assert box.published_calls == 1


def test_provision_refuses_to_publish_other_peoples_staged_changes(box):
    box.other_staged = ["someone_elses_module"]
    r = provision_module("heists", fields=HEIST_FIELDS)
    assert r.status == "partial" and box.published_calls == 0
    assert any("someone_elses_module" in f.claim for f in r.findings)


def test_provision_refuses_a_non_identifier(box):
    assert provision_module("x/../alerts").status == "blocked"


# --- orient --------------------------------------------------------------------

def test_orient_reports_fields_access_and_connectors(box):
    box.add("heists", fields=HEIST_FIELDS)
    box.add("staged_only", published=False)
    r = orient(goal="enrich an ip", modules=["heists", "staged_only"])
    claims = " | ".join(f.claim for f in r.findings)
    assert "heists has 5 fields" in claims and "codename" in claims
    assert "can read heists" in claims
    assert "cannot read staged_only: unpublished" in claims
    assert "1 connectors configured; healthy: virustotal" in claims
    assert r.data["modules"]["heists"]["access"]["read"] == "ok"


# --- repair_playbook chained from debug_run -------------------------------------

_IRI = "/api/3/alerts/1daad405-3999-4682-ba60-586bec063d25"
_TEMPLATE = "/api/3/alerts/{{ vars.steps.Find_Latest_Alert[0]['@id'] }}"
_YAML = f"""playbooks:
  - name: Refresh Latest Alert Details
    steps:
      - name: Get Alert Details
        type: connector
        connector: cyops_utilities
        operation: make_cyops_request
        params:
          iri: "{_TEMPLATE}"
"""


def test_repair_fixes_the_doubled_iri_debug_run_found(box, monkeypatch):
    monkeypatch.setattr(D, "_diagnose", lambda p, y: {
        "ok": False, "pb_execution": "122386", "run_status": "failed",
        "failing_step": "Get Alert Details",
        "error_message": f"404 for url: https://localhost/api/3/alerts/{_IRI}",
        "step_diagnostics": [{"step": "Get Alert Details", "arg_path": "params.iri",
                              "ok": True, "code": "ok", "template": _TEMPLATE,
                              "rendered": f"/api/3/alerts/{_IRI}"}]})
    import fsr_playbooks.mcp_server.tools_compile as TC
    monkeypatch.setattr(TC, "validate_yaml", lambda y: {"ok": True})
    dbg = D.debug_run("Refresh Latest Alert Details")
    r = repair_playbook(dbg.id, _YAML)
    assert r.follow_up_of == dbg.id
    assert "/api/3/alerts/{{" not in r.data["yaml"]
    assert "{{ vars.steps.Find_Latest_Alert[0]['@id'] }}" in r.data["yaml"]
    assert any("Fixed the doubled IRI" in f.claim for f in r.findings)
    # The fix cites the debug run's own evidence (reloaded, not redone).
    assert set(r.findings[0].evidence) <= set(dbg.evidence)


def test_repair_needs_a_debug_run_result(box):
    assert repair_playbook("nope", _YAML).status == "blocked"


# --- investigate_alert ----------------------------------------------------------

def test_investigate_lists_indicators_and_enrichment(box, monkeypatch):
    import fsr_playbooks.mcp_server.tools_connector_discovery as CD
    import fsr_playbooks.mcp_server.tools_triage as TT
    monkeypatch.setattr(TT, "get_record", lambda iri: {"ok": True, "record": {
        "name": "C2 egress", "sourceIp": "192.0.2.10", "severity": "High"}})
    monkeypatch.setattr(CD, "find_enrichment_actions", lambda target_type, probe: {
        "actions": [{"connector": "virustotal", "operation": "get_ip_reputation", "tier": 1}]})
    r = investigate_alert("/api/3/alerts/u1")
    assert r.data["indicators"] == {"ip": ["192.0.2.10"]}
    assert any("get_ip_reputation" in n.action for n in r.next)
    assert r.status == "partial"  # no verdict until the lookups run


def test_a_verdict_card_becomes_the_same_result_shape():
    reg = {"toolu_1": {"name": "run_op", "ok": True, "args": {"ip": "192.0.2.10"}},
           "toolu_2": {"name": "get_record", "ok": False}}
    card = {"disposition": "benign", "confidence": 0.9, "summary": "scanner",
            "findings": [{"claim": "known scanner", "evidence": ["toolu_1"]}],
            "unknowns": []}
    r = from_verdict(card, reg)
    assert r.workflow == "investigate_alert" and r.status == "answered"
    assert r.findings[0].confidence == "high" and r.data["disposition"] == "benign"
    assert "toolu_2" not in r.evidence


# --- the heists use case: provision -> build -> verify ---------------------------

def test_heist_use_case_chain(box):
    prov = provision_module("heists", label="Heist", fields=HEIST_FIELDS)
    assert prov.status == "answered"

    build = build_playbook("plan the job when a heist is logged", HEIST_YAML,
                           modules=["heists"])
    assert build.data["verify"]["ready_to_push"], build.data["verify"]["required_fixes"]
    assert any(n.action.startswith("push_playbook") and n.tier == 3 for n in build.next)

    # The playbook ran on a test record and stamped its status.
    box.modules["heists"]["records"]["h1"] = {"uuid": "h1", "codename": "Bellagio",
                                              "status": "Briefed"}
    done = verify_effect("heists", "h1", {"status": "Briefed"})
    assert done.status == "answered"


def test_build_without_the_module_on_the_box_is_not_ready(box):
    build = build_playbook("plan the job", HEIST_YAML, modules=[])
    assert not build.data["verify"]["ready_to_push"]
    assert any("heists" in f.claim for f in build.findings)
    assert any(n.action.startswith("playbook_yaml_reference") for n in build.next)


def test_build_without_a_draft_points_at_the_yaml_reference(box):
    build = build_playbook("plan the job", modules=["heists"])
    assert any(n.action == "playbook_yaml_reference()" for n in build.next)

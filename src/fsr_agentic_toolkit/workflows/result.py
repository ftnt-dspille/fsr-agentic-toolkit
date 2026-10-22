"""The one result shape every workflow returns, and how a workflow builds it.

A workflow (debug_run, verify_effect, orient, ...) gathers evidence from the
box, states findings that cite that evidence, says what it could not determine,
and suggests the next call. The CLI and the MCP tool render the same object.

Evidence ids are checked by the same rule verdicts use
(``_citation_validator.check_evidence_ids``): a finding citing an id the
workflow never produced, or one from a failed lookup, is refused.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from fsr_playbooks.mcp_server._citation_validator import (
    TurnEvidence,
    check_evidence_ids,
)

EvidenceKind = Literal["record", "run", "step", "query", "op", "audit", "module",
                       "playbook", "schema", "permission"]
Status = Literal["answered", "partial", "blocked"]
Confidence = Literal["high", "medium", "low"]


class Evidence(BaseModel):
    kind: EvidenceKind
    ref: str  # an IRI, run pk, step name or query: something a reader can open
    excerpt: str = ""


class Finding(BaseModel):
    claim: str
    evidence: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"


class NextStep(BaseModel):
    action: str  # a tool call, e.g. "repair_playbook(follow_up=<id>)"
    why: str
    tier: int = 1


class WorkflowResult(BaseModel):
    workflow: str
    id: str
    status: Status
    findings: list[Finding] = Field(default_factory=list)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    next: list[NextStep] = Field(default_factory=list)
    follow_up_of: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)  # workflow-specific payload

    def to_tool(self) -> dict[str, Any]:
        """The MCP / LLM tool envelope: ok + the result fields."""
        return {"ok": self.status != "blocked", **self.model_dump(exclude_none=True)}


class InvalidCitation(ValueError):
    """A finding cited evidence the workflow never produced."""

    def __init__(self, err: dict[str, Any]):
        super().__init__(err.get("message") or str(err))
        self.err = err


class WorkflowRun:
    """Builds one WorkflowResult. Evidence lives in a TurnEvidence registry so
    findings are validated by the verdict rule."""

    def __init__(self, workflow: str, *, follow_up: WorkflowResult | None = None):
        self.workflow = workflow
        self.id = f"{workflow}-{uuid.uuid4().hex[:12]}"
        self._registry = TurnEvidence()
        self._evidence: dict[str, Evidence] = {}
        self._findings: list[Finding] = []
        self._unknowns: list[str] = []
        self._next: list[NextStep] = []
        self.data: dict[str, Any] = {}
        self.follow_up_of = follow_up.id if follow_up else None
        if follow_up is not None:
            # A follow-up reloads the earlier evidence instead of redoing it.
            for eid, ev in follow_up.evidence.items():
                self._add(eid, ev, ok=True)

    def _add(self, eid: str, ev: Evidence, *, ok: bool) -> str:
        self._registry.register(eid, f"{self.workflow}:{ev.kind}", ok)
        if ok:
            self._evidence[eid] = ev
        return eid

    def evidence(self, kind: EvidenceKind, ref: str, excerpt: str = "",
                 *, ok: bool = True) -> str:
        """Record one piece of evidence; returns its id. ``ok=False`` records a
        lookup that failed, which no finding may cite."""
        eid = f"e{len(self._registry.valid_ids()) + 1}"
        return self._add(eid, Evidence(kind=kind, ref=str(ref),
                                       excerpt=str(excerpt)[:600]), ok=ok)

    def finding(self, claim: str, evidence: list[str],
                confidence: Confidence = "medium") -> None:
        if not evidence:
            raise InvalidCitation({"code": "uncited_finding",
                                   "message": f"finding cites no evidence: {claim!r}"})
        err = check_evidence_ids(self._registry, list(evidence))
        if err is not None:
            raise InvalidCitation(err)
        self._findings.append(Finding(claim=claim, evidence=list(evidence),
                                      confidence=confidence))

    def unknown(self, what: str) -> None:
        self._unknowns.append(what)

    def next(self, action: str, why: str, tier: int = 1) -> None:
        self._next.append(NextStep(action=action, why=why, tier=tier))

    def result(self, status: Status | None = None) -> WorkflowResult:
        if status is None:
            status = ("answered" if self._findings and not self._unknowns
                      else "partial" if self._findings else "blocked")
        out = WorkflowResult(
            workflow=self.workflow, id=self.id, status=status,
            findings=self._findings, evidence=self._evidence,
            unknowns=self._unknowns, next=self._next,
            follow_up_of=self.follow_up_of, data=self.data)
        result_store().put(out)
        return out


# ---------------------------------------------------------------------------
# Follow-up store: result id -> WorkflowResult
# ---------------------------------------------------------------------------

class ResultStore(Protocol):
    def put(self, result: WorkflowResult) -> None: ...
    def get(self, result_id: str) -> WorkflowResult | None: ...


class MemoryResultStore:
    """Default store: this process only (MCP server, one CLI session)."""

    def __init__(self) -> None:
        self._rows: dict[str, WorkflowResult] = {}
        self._lock = threading.Lock()

    def put(self, result: WorkflowResult) -> None:
        with self._lock:
            self._rows[result.id] = result

    def get(self, result_id: str) -> WorkflowResult | None:
        return self._rows.get(result_id)


class SqliteResultStore:
    """Durable store, for hosts whose turns span processes (the connector)."""

    def __init__(self, path: str | Path):
        self._path = str(path)
        with sqlite3.connect(self._path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS workflow_results "
                      "(id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def put(self, result: WorkflowResult) -> None:
        with sqlite3.connect(self._path) as c:
            c.execute("INSERT OR REPLACE INTO workflow_results VALUES (?, ?)",
                      (result.id, result.model_dump_json()))

    def get(self, result_id: str) -> WorkflowResult | None:
        with sqlite3.connect(self._path) as c:
            row = c.execute("SELECT body FROM workflow_results WHERE id = ?",
                            (result_id,)).fetchone()
        return WorkflowResult.model_validate_json(row[0]) if row else None


_STORE: ResultStore = MemoryResultStore()


def set_result_store(store: ResultStore) -> None:
    global _STORE
    _STORE = store


def result_store() -> ResultStore:
    return _STORE


def load_follow_up(result_id: str | None, *, workflow: str | None = None
                   ) -> tuple[WorkflowResult | None, dict[str, Any] | None]:
    """``(result, None)`` for a known id, ``(None, None)`` for no id, else
    ``(None, refusal)``. ``workflow`` restricts which result kind is accepted."""
    if not result_id:
        return None, None
    found = result_store().get(result_id)
    if found is None:
        return None, {"ok": False, "code": "unknown_follow_up",
                      "message": f"no workflow result {result_id!r} in this session"}
    if workflow and found.workflow != workflow:
        return None, {"ok": False, "code": "wrong_follow_up",
                      "message": f"{result_id!r} is a {found.workflow} result; "
                                 f"this needs a {workflow} result"}
    return found, None


# ---------------------------------------------------------------------------
# Rendering (CLI text; MCP returns to_tool())
# ---------------------------------------------------------------------------

def render_text(result: WorkflowResult) -> str:
    lines = [f"{result.workflow} [{result.status}]  id={result.id}"]
    if result.follow_up_of:
        lines.append(f"follows {result.follow_up_of}")
    if result.findings:
        lines.append("")
        lines.append("Findings:")
        for f in result.findings:
            lines.append(f"  - ({f.confidence}) {f.claim}  [{', '.join(f.evidence)}]")
    if result.evidence:
        lines.append("")
        lines.append("Evidence:")
        for eid, ev in result.evidence.items():
            ex = f": {ev.excerpt}" if ev.excerpt else ""
            lines.append(f"  {eid} {ev.kind} {ev.ref}{ex}")
    if result.unknowns:
        lines.append("")
        lines.append("Unknown:")
        lines.extend(f"  - {u}" for u in result.unknowns)
    if result.next:
        lines.append("")
        lines.append("Next:")
        lines.extend(f"  - {n.action} (tier {n.tier}): {n.why}" for n in result.next)
    return "\n".join(lines)


def render_json(result: WorkflowResult) -> str:
    return json.dumps(result.to_tool(), indent=2, default=str)

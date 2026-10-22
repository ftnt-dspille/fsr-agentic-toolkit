"""investigate_alert: gather an alert's evidence, and make its verdict the result.

``investigate_alert`` does the deterministic half: reads the record, pulls the
indicators off it, and lists the enrichment lookups configured here for each
indicator type. It does not judge; the judging model calls those lookups and
emits a verdict card. ``from_verdict`` turns that delivered card (findings
already citing tool-call ids) into a WorkflowResult with the same evidence, so
an outside agent and the on-box assistant get one shape.
"""
from __future__ import annotations

import re
from typing import Any

from .result import Evidence, WorkflowResult, WorkflowRun

# Record fields that carry indicators, by enrichment target type.
_INDICATOR_FIELDS: dict[str, tuple[str, ...]] = {
    "ip": ("sourceIp", "destinationIp", "sourceip", "destinationip", "ipAddress"),
    "host": ("hostName", "computerName", "deviceName", "sourceHost", "destinationHost"),
    "user": ("userName", "sourceUser", "targetUser", "user"),
    "domain": ("domain", "destinationDomain"),
    "url": ("url", "destinationUrl"),
    "hash": ("fileHash", "md5", "sha1", "sha256"),
    "email": ("emailFrom", "senderEmail", "emailTo"),
}
_IRI = re.compile(r"^/api/3/[A-Za-z0-9_]+/[A-Za-z0-9-]+$")


def _indicators(record: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind, names in _INDICATOR_FIELDS.items():
        vals = []
        for n in names:
            v = record.get(n)
            if isinstance(v, str) and v.strip() and v.strip() not in vals:
                vals.append(v.strip())
        if vals:
            out[kind] = vals
    return out


def investigate_alert(iri: str) -> WorkflowResult:
    run = WorkflowRun("investigate_alert")
    if not _IRI.fullmatch(iri or ""):
        run.unknown(f"{iri!r} is not a record IRI (/api/3/<module>/<uuid>)")
        return run.result("blocked")
    from fsr_playbooks.mcp_server.tools_triage import get_record
    got = get_record(iri=iri)
    if not got.get("ok"):
        run.evidence("record", iri, str(got.get("message") or got)[:200], ok=False)
        run.unknown(f"could not read {iri}: {got.get('code')}")
        return run.result("blocked")
    rec = got.get("record") or {}
    rev = run.evidence("record", iri, ", ".join(
        f"{k}={rec.get(k)}" for k in ("name", "severity", "status", "type") if rec.get(k)))
    inds = _indicators(rec)
    run.data["indicators"] = inds
    if not inds:
        run.finding("the record carries no indicator fields to enrich", [rev], "medium")
        run.unknown("no indicators: pivot to related records or the source system")
        return run.result()
    run.finding("indicators on the record: " + "; ".join(
        f"{k}: {', '.join(v)}" for k, v in inds.items()), [rev], "high")

    from fsr_playbooks.mcp_server.tools_connector_discovery import (
        find_enrichment_actions,
    )
    for kind, values in inds.items():
        acts = find_enrichment_actions(target_type=kind, probe=True)
        rows = acts.get("actions") or acts.get("results") or []
        if not rows:
            run.unknown(f"no configured, healthy enrichment for {kind}")
            continue
        aev = run.evidence("op", f"enrichment for {kind}", str(rows)[:400])
        run.finding(f"{len(rows)} configured enrichment lookups for {kind}", [aev], "medium")
        for row in rows[:3]:
            if isinstance(row, dict):
                run.next(f"run_op(connector='{row.get('connector')}', "
                         f"operation='{row.get('operation') or row.get('op')}', "
                         f"params=<{kind}={values[0]}>)",
                         f"enrich {values[0]}", int(row.get("tier") or 1))
    run.unknown("no verdict yet: run the lookups, then emit_verdict; "
                "from_verdict turns the card into this result shape")
    return run.result()


def from_verdict(card: dict[str, Any], turn_registry: dict[str, dict[str, Any]]
                 ) -> WorkflowResult:
    """A delivered verdict card as a WorkflowResult. ``turn_registry`` is the
    turn's ``TurnEvidence.valid_ids()``; each cited id becomes evidence."""
    run = WorkflowRun("investigate_alert")
    for eid, info in (turn_registry or {}).items():
        run._add(eid, Evidence(kind="op", ref=str(info.get("name") or "?"),
                               excerpt=str(info.get("args") or info.get("finding") or "")[:300]),
                 ok=info.get("ok") is True)
    conf = card.get("confidence")
    level = ("high" if isinstance(conf, (int, float)) and conf >= 0.8
             else "low" if isinstance(conf, (int, float)) and conf < 0.5 else "medium")
    for f in card.get("findings") or []:
        run.finding(str(f.get("claim")), list(f.get("evidence") or []), level)
    for u in card.get("unknowns") or []:
        run.unknown(str(u))
    for a in card.get("recommended_actions") or []:
        run.next(str(a.get("tool") or a.get("label")), str(a.get("label") or ""),
                 int(a.get("tier") or 3))
    run.data.update({k: card.get(k) for k in ("disposition", "severity", "confidence",
                                              "summary") if k in card})
    return run.result()

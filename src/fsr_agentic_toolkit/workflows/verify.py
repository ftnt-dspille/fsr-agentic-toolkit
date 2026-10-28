"""verify_write workflow: after a write, read the box back and say what actually changed.

A write tool reporting success proves nothing: the platform ignores unknown
keys and answers 200, a picklist label can be refused after approval, a
playbook can finish without touching the record. This reads the record (or
runs declarative assertions) and reports each expected field as a finding.
Any mismatch makes the result ``partial`` with the field named.
"""
from __future__ import annotations

from typing import Any

from fsr_playbooks.mcp_server import _shared
from fsr_playbooks.mcp_server._record_lookup import live_request

from .result import WorkflowResult, WorkflowRun


def _plain(v: Any) -> Any:
    """A record field as a comparable value: picklist -> label, link -> IRI."""
    if isinstance(v, dict):
        return v.get("itemValue") or v.get("@id") or v.get("name") or v
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _matches(actual: Any, expected: Any) -> bool:
    a, e = _plain(actual), _plain(expected)
    if isinstance(a, str) and isinstance(e, str):
        if a.strip().lower() == e.strip().lower():
            return True
        # An IRI compared with its uuid (or a shorter IRI) is the same link.
        if a.startswith("/api/") or e.startswith("/api/"):
            return a.rstrip("/").endswith(e.rstrip("/")) or e.rstrip("/").endswith(a.rstrip("/"))
        return False
    if isinstance(a, list) and isinstance(e, list):
        return sorted(map(str, a)) == sorted(map(str, e))
    return a == e


def _read_record(module: str, uuid: str) -> tuple[dict | None, str]:
    client = _shared._live_client()
    if client is None:
        return None, "no FortiSOAR instance is configured"
    r = live_request("get", client, f"{client.base_url}/api/3/{module}/{uuid}")
    code = getattr(r, "status_code", None)
    if code != 200:
        return None, f"HTTP {code} reading /api/3/{module}/{uuid}"
    body = r.json()
    return (body if isinstance(body, dict) else None), ""


def verify_effect(module: str = "", uuid: str = "", fields: dict | None = None,
                  assertions: list[dict] | None = None) -> WorkflowResult:
    run = WorkflowRun("verify_write")
    if not ((module and uuid and fields) or assertions):
        run.unknown("nothing to verify: pass module + uuid + fields, or assertions")
        return run.result("blocked")

    mismatched = False
    if module and uuid and fields:
        rec, err = _read_record(module, uuid)
        iri = f"/api/3/{module}/{uuid}"
        if rec is None:
            run.evidence("record", iri, err, ok=False)
            run.unknown(f"could not read {iri}: {err}")
        else:
            for name, want in fields.items():
                got = rec.get(name)
                ev = run.evidence("record", f"{iri}#{name}", f"{name}={_plain(got)!r}")
                if name not in rec:
                    mismatched = True
                    run.finding(f"{name!r} is not a field on the record, so the write "
                                f"did not set it", [ev], "high")
                elif _matches(got, want):
                    run.finding(f"{name} is {_plain(got)!r} as expected", [ev], "high")
                else:
                    mismatched = True
                    run.finding(f"{name} is {_plain(got)!r}, expected {_plain(want)!r}: "
                                f"the write did not take effect", [ev], "high")

    if assertions:
        from fsr_playbooks.mcp_server.tools_recipe import assert_playbook_outcome
        out = assert_playbook_outcome(assertions)
        if out.get("code"):
            run.unknown(f"{out.get('code')}: {out.get('message')}")
        for a, r in zip(assertions, out.get("results") or [], strict=False):
            ev = run.evidence("query", f"{a.get('kind')} {a.get('module')} {a.get('filters')}",
                              str(r.get("observed", r.get("observed_count", "")))[:300])
            if r.get("ok"):
                run.finding(f"{a.get('kind')} on {a.get('module')} holds", [ev], "high")
            else:
                mismatched = True
                run.finding(f"{a.get('kind')} on {a.get('module')} does not hold: "
                            f"{r.get('message')}", [ev], "high")

    if mismatched:
        run.next("debug_playbook_run(...) or repair_playbook(...)",
                 "the effect is missing; find why before retrying the write", 1)
        return run.result("partial")
    return run.result()

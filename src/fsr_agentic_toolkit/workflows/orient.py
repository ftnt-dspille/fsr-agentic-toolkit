"""orient: what this box has for a goal, in one call.

The real field names and picklist values of the modules involved, whether the
caller can reach each module (and if not, which break it is), and the
connectors that are configured and healthy for the goal. Removes the
guessed-field-name and silent-zero-results failure class.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fsr_playbooks.mcp_server import _shared
from fsr_playbooks.mcp_server._record_lookup import live_request

from . import access as A
from .result import WorkflowResult, WorkflowRun

_PICKLIST_VALUES = 5  # picklist fields per module whose values are listed


def _module_meta(client: Any, module: str) -> tuple[dict | None, int | None]:
    r = live_request("get", client, f"{client.base_url}/api/3/model_metadatas"
                     f"?type={quote(module)}&$relationships=true&$limit=1")
    status = getattr(r, "status_code", None)
    if status != 200:
        return None, status
    members = (r.json() or {}).get("hydra:member") or []
    return (members[0] if members else None), status


def _connectors(goal: str) -> dict[str, Any]:
    from fsr_playbooks.mcp_server.tools_connector_discovery import (
        list_configured_connectors,
    )
    out = list_configured_connectors(probe=True)
    if goal:
        try:
            from fsr_playbooks.mcp_server.tools_find import find
            out = {**out, "actions": find(kind="action", query=goal)}
        except Exception as exc:  # noqa: BLE001
            out = {**out, "actions_error": repr(exc)}
    return out


def orient(goal: str = "", modules: list[str] | None = None,
           connectors: bool = True) -> WorkflowResult:
    run = WorkflowRun("survey_box")
    client = _shared._live_client()
    if client is None:
        run.unknown("no FortiSOAR instance is configured")
        return run.result("blocked")
    run.data["modules"] = {}

    for module in modules or []:
        if not A._IDENT.fullmatch(module or ""):
            run.unknown(f"{module!r} is not a module name")
            continue
        meta, status = _module_meta(client, module)
        if meta is None:
            run.evidence("schema", module, f"HTTP {status}", ok=False)
            run.unknown(f"{module}: no published or staged schema found (HTTP {status})")
            continue
        attrs = [a for a in meta.get("attributes") or [] if isinstance(a, dict)]
        fields = {a["name"]: a.get("type") for a in attrs if a.get("name")}
        from fsr_playbooks.mcp_server.tools_verify import note_live_module
        note_live_module(module, fields)
        schema_ev = run.evidence("schema", module,
                                 ", ".join(f"{k}:{v}" for k, v in list(fields.items())[:40]))
        info: dict[str, Any] = {"fields": fields, "picklists": {}}
        from fsr_playbooks.mcp_server.tools_records import (
            _field_picklist,
            _picklist_items,
        )
        for a in attrs:
            pl = _field_picklist(a)
            if pl and len(info["picklists"]) < _PICKLIST_VALUES:
                info["picklists"][a["name"]] = [i["itemValue"] for i in _picklist_items(client, pl)]
        run.finding(f"{module} has {len(fields)} fields: "
                    + ", ".join(list(fields)[:25]), [schema_ev], "high")
        if info["picklists"]:
            run.finding(f"{module} picklist values: " + "; ".join(
                f"{k}={v}" for k, v in info["picklists"].items()), [schema_ev], "high")

        acc, code, excerpt = A.probe_read(module, client)
        acc_ev = run.evidence("permission", f"/api/3/{module}", f"HTTP {code}: {excerpt[:120]}")
        info["access"] = {"read": acc}
        if acc == "ok":
            run.finding(f"the caller can read {module}", [acc_ev], "high")
        elif acc != "unknown":
            run.finding(f"the caller cannot read {module}: {acc} ({A.fix_for(acc)})",
                        [acc_ev], "high")
            run.next(f"provision_module(module='{module}')" if acc != "team"
                     else "update_record(...)", A.fix_for(acc), 3)
        else:
            run.unknown(f"{module}: read access could not be determined (HTTP {code})")
        # Create permission needs a write to prove; provision_module does that.
        info["access"]["create"] = "not probed (provision_module verifies it)"
        run.data["modules"][module] = info

    if connectors:
        try:
            out = _connectors(goal)
            rows = out.get("connectors") or out.get("configured") or []
            names = [r.get("name") if isinstance(r, dict) else str(r) for r in rows]
            ev = run.evidence("op", "configured connectors", ", ".join(map(str, names))[:500])
            healthy = [r.get("name") for r in rows if isinstance(r, dict)
                       and str(r.get("status") or r.get("health") or "").lower()
                       in ("available", "ok", "connected")]
            run.finding(f"{len(names)} connectors configured; healthy: "
                        f"{', '.join(map(str, healthy)) or 'none reported'}", [ev], "medium")
            if goal and isinstance(out.get("actions"), dict):
                acts = out["actions"].get("results") or out["actions"].get("actions") or []
                if acts:
                    aev = run.evidence("op", f"actions for {goal!r}", str(acts)[:500])
                    run.finding(f"{len(acts)} configured actions match {goal!r}", [aev], "medium")
                else:
                    run.unknown(f"no configured, healthy action matches {goal!r}")
            run.data["connectors"] = out
        except Exception as exc:  # noqa: BLE001
            run.unknown(f"connectors could not be listed: {exc}")
    return run.result()

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


_STOP = frozenset({"a", "an", "and", "the", "to", "of", "on", "in", "for", "with", "when", "then",
                  "send", "get", "create", "make", "new", "from", "that", "this", "is", "it"})


def _connectors(client: Any, goal: str) -> dict[str, Any]:
    """Connectors with a saved configuration on THIS box, read through the bound client.

    The library's own lister builds its client from FSR_BASE_URL, so on a multi-instance
    server it answered for whichever box the environment named (or none), and healthchecked
    every connector serially (50-80s). One paged listing is ~6s and answers for the
    instance the caller asked about. Health is not probed here: ``healthcheck_connector``
    does that for the few the caller picks."""
    rows = []
    for c in client.connectors.list_configured():
        if (c.config_count or 0) > 0:
            rows.append({"name": c.name, "label": c.label, "version": c.version,
                         "config_count": c.config_count, "status": c.status})
    words = {w for w in (goal or "").lower().replace("-", " ").split() if len(w) > 2 and w not in _STOP}
    matches = [r["name"] for r in rows
               if words & set(f"{r['name']} {r['label'] or ''}".lower().replace("_", " ")
                              .replace("-", " ").split())]
    return {"connectors": rows, "matches": matches}


_CACHE_SECONDS = 120.0
_cache: dict[tuple, tuple[float, WorkflowResult]] = {}


def _box_context(client: Any, run: WorkflowRun) -> None:
    """Version, FortiAI state and registered MCP servers: the facts an agent
    otherwise rebuilds over a dozen calls. Each read is best-effort."""
    box: dict[str, Any] = {}
    try:
        box["version"] = str(client.version())
    except Exception as exc:  # noqa: BLE001
        run.unknown(f"version could not be read: {exc}")
    try:
        box["ai_enabled"] = client.ai.features_enabled()
        servers = client.ai.list_mcp_servers() or []
        box["mcp_servers"] = [s.get("name") if isinstance(s, dict) else getattr(s, "name", str(s))
                              for s in servers]
    except Exception as exc:  # noqa: BLE001 - FortiAI may not be installed
        run.unknown(f"FortiAI configuration could not be read: {exc}")
    if box:
        ev = run.evidence("op", "box context", ", ".join(f"{k}={v}" for k, v in box.items())[:500])
        parts = []
        if "version" in box:
            parts.append(f"FortiSOAR {box['version']}")
        if "ai_enabled" in box:
            parts.append(f"FortiAI {'enabled' if box['ai_enabled'] else 'disabled'}")
        if box.get("mcp_servers"):
            parts.append(f"{len(box['mcp_servers'])} registered MCP servers ({', '.join(map(str, box['mcp_servers'][:8]))})")
        run.finding("; ".join(parts) or "box context read", [ev], "high")
    run.data["box"] = box


def orient(goal: str = "", modules: list[str] | None = None,
           connectors: bool = True, fresh: bool = False) -> WorkflowResult:
    import time

    client = _shared._live_client()
    key = (id(client), goal, tuple(modules or []), connectors)
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    result = _orient(client, goal, modules, connectors)
    if result.status != "blocked":
        _cache[key] = (time.monotonic(), result)
    return result


def _orient(client: Any, goal: str, modules: list[str] | None, connectors: bool) -> WorkflowResult:
    run = WorkflowRun("survey_box")
    if client is None:
        run.unknown("no FortiSOAR instance is configured")
        return run.result("blocked")
    _box_context(client, run)
    run.data["modules"] = {}
    if not modules:
        # An empty dict reads as "the box has no modules"; say why it is empty.
        run.unknown("no modules were named, so none were described; "
                    "list_modules lists every module, survey_box(modules=[...]) describes some")
        run.next("list_modules()", "every module on the box, by display name and API type", 2)

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
            out = _connectors(client, goal)
            rows = out["connectors"]
            names = [r["name"] for r in rows]
            ev = run.evidence("op", "configured connectors", ", ".join(map(str, names))[:500])
            run.finding(f"{len(names)} connectors have a saved configuration on this box "
                        "(health not probed)", [ev], "high")
            if goal and out["matches"]:
                mev = run.evidence("op", f"connectors matching {goal!r}", ", ".join(out["matches"]))
                run.finding(f"configured connectors whose name or label matches the goal: "
                            f"{', '.join(out['matches'])}", [mev], "medium")
                run.next(f"healthcheck_connector(connector='{out['matches'][0]}')",
                         "confirm the connector you will use is reachable", 1)
                run.next(f"list_connector_operations(connector='{out['matches'][0]}')",
                         "the operations it offers, read live from this box", 1)
            elif goal:
                run.unknown(f"no configured connector's name or label matches {goal!r}; "
                            "list_connectors shows everything installed")
            run.data["connectors"] = out
        except Exception as exc:  # noqa: BLE001
            run.unknown(f"connectors could not be listed: {exc}")
    return run.result()

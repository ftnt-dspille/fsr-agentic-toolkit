"""Can the caller reach a module, and if not, which of the three breaks is it?

A module the caller cannot use fails in three ways that look alike from a
generic error and need different fixes:

- ``unpublished``: the module is staged but never published. The box answers
  404 "No route found" for its records path.
- ``role``: published, but the caller's role has no permission on it (403
  "Access Denied"). A role made before the module never covers it.
- ``team``: the role allows it, but the record is owned by a team the caller
  is not on (403 naming a team, or an empty result where a record exists).
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fsr_playbooks.mcp_server import _shared
from fsr_playbooks.mcp_server._record_lookup import live_request

_IDENT = re.compile(r"[A-Za-z0-9_]+")

Access = Literal["ok", "unpublished", "role", "team", "unknown"]

_FIX = {
    "unpublished": "publish the module (modules_admin.publish; appliance-wide, so "
                   "first confirm pending_changes holds only this module)",
    "role": "grant the caller's role read/create/update on the module "
            "(roles.grant_module_permissions), or do it in the UI",
    "team": "put the record on a team the caller is on, or add the caller to the "
            "record's team",
}


def fix_for(access: Access) -> str:
    return _FIX.get(access, "")


def classify(status: int | None, text: str = "") -> Access:
    """The break a records-path response shows."""
    low = (text or "").lower()
    if status == 200:
        return "ok"
    if status == 404 and "no route found" in low:
        return "unpublished"
    if status == 403:
        return "team" if "team" in low else "role"
    if status == 401:
        return "unknown"
    return "unknown"


def probe_read(module: str, client: Any | None = None) -> tuple[Access, int | None, str]:
    """``(access, http_status, excerpt)`` for reading ``module``'s records."""
    client = client if client is not None else _shared._live_client()
    if client is None:
        return "unknown", None, "no FortiSOAR instance is configured"
    try:
        r = live_request("get", client, f"{client.base_url}/api/3/{module}?$limit=1")
    except Exception as exc:  # noqa: BLE001
        return "unknown", None, f"transport error: {exc}"
    status = getattr(r, "status_code", None)
    text = getattr(r, "text", "") or ""
    return classify(status, text), status, text[:200]


def probe_create(module: str, record: dict[str, Any],
                 client: Any | None = None) -> tuple[Access, int | None, str]:
    """Create one probe record and delete it again. A real write: only the
    tier-3 provision_module calls this."""
    client = client if client is not None else _shared._live_client()
    if client is None:
        return "unknown", None, "no FortiSOAR instance is configured"
    base = f"{client.base_url}/api/3/{module}"
    r = live_request("post", client, base, json=record)
    status = getattr(r, "status_code", None)
    text = getattr(r, "text", "") or ""
    if status in (200, 201):
        try:
            iri = (r.json() or {}).get("@id")
        except Exception:  # noqa: BLE001
            iri = None
        if iri:
            live_request("delete", client, f"{client.base_url}{iri}")
        return "ok", status, "probe record created and removed"
    return classify(status, text), status, text[:200]

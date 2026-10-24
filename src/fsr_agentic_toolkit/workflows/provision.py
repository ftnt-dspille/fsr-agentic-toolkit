"""provision_module: create a module and leave it usable by the caller.

create -> confirm the staged changes are only this module (publishing is
appliance-wide) -> publish -> grant the role(s) -> prove the caller can read
and create there. Each step is evidence; a step that cannot be done stops the
run with the break named. ``check_only`` makes no writes: it names which of
the three breaks (unpublished, role, team) the module has and the fix.

Writes schema, publishes and changes a role, so the tool is tier 3. Uses the
pyfsr typed APIs on the live client (``modules_admin``, ``roles``); a client
without them (an on-box loopback that does not carry them) is reported, not
worked around.
"""
from __future__ import annotations

from typing import Any

from fsr_playbooks.mcp_server import _shared

from .._fields import coerce_field_specs
from . import access as A
from .result import WorkflowResult, WorkflowRun

DEFAULT_ROLES = ("Full App Permissions",)


#: Seconds between probes while the box is mid-publish.
IDLE_INTERVAL = 5.0


def _when_idle(read: Any, states: list[str], timeout: float = 600.0,
               interval: float | None = None) -> Any:
    """``read()`` once the box is not mid-publish. Each distinct progress state
    the box answers with (a 5xx / migrate marker) is appended to ``states``;
    any other error propagates. Raises TimeoutError naming the last state."""
    import time

    from pyfsr.exceptions import is_migrate_transient

    deadline = time.monotonic() + timeout
    while True:
        try:
            return read()
        except Exception as exc:
            if not is_migrate_transient(exc):
                raise
            state = str(getattr(exc, "message", "") or exc).strip()[:120] or "publish in progress"
            if not states or states[-1] != state:
                states.append(state)
            if time.monotonic() >= deadline:
                raise TimeoutError(state) from exc
            time.sleep(IDLE_INTERVAL if interval is None else interval)


def _modname(p: Any) -> str:
    return str(getattr(p, "module", None) or (p.get("module") if isinstance(p, dict) else p))


def provision_module(module: str, label: str = "", plural: str = "",
                     fields: list[dict] | None = None, roles: list[str] | None = None,
                     probe_record: dict | None = None, check_only: bool = False
                     ) -> WorkflowResult:
    run = WorkflowRun("provision_module")
    run.data["module"] = module
    if not A._IDENT.fullmatch(module or ""):
        run.unknown(f"{module!r} is not a module name (letters, digits, _)")
        return run.result("blocked")
    client = _shared._live_client()
    admin = getattr(client, "modules_admin", None)
    role_api = getattr(client, "roles", None)
    if client is None or admin is None:
        run.unknown("this client cannot administer modules (no modules_admin); "
                    "run with a pyfsr client against the box")
        return run.result("blocked")

    published = bool(admin.is_published(module))
    staged = admin.get_staging(module) is not None
    state_ev = run.evidence("module", module, f"published={published} staged={staged}")

    if check_only:
        acc, code, excerpt = A.probe_read(module, client)
        ev = run.evidence("permission", f"/api/3/{module}", f"HTTP {code}: {excerpt[:120]}")
        if not published:
            acc = "unpublished"
        if acc == "ok":
            run.finding(f"the caller can read {module}", [state_ev, ev], "high")
        elif acc == "unknown":
            run.unknown(f"read access to {module} could not be determined (HTTP {code})")
        else:
            run.finding(f"{module} is not usable: {acc}. Fix: {A.fix_for(acc)}",
                        [state_ev, ev], "high")
            run.next(f"provision_module(module='{module}')", A.fix_for(acc), 3)
        run.data["access"] = acc
        return run.result()

    # 1. create
    if not (published or staged):
        admin.create_module(module, label=label or None, plural=plural or None,
                            fields=coerce_field_specs(fields))
        run.finding(f"created {module} (staged)",
                    [run.evidence("module", module, "create_module")], "high")

    # 2. publish, only when nothing else is staged. A box already mid-publish
    # answers 503 progress states ("Generating Models", "Clearing Cache", ...):
    # wait them out and report them, never return them as errors.
    states: list[str] = []
    try:
        pending_rows = _when_idle(admin.pending_changes, states)
    except TimeoutError as exc:
        run.unknown(f"the box stayed busy with another publish: {exc}")
        run.next("provision_module(...) again once the publish finishes",
                 "the box is mid-publish", 3)
        return run.result("blocked")
    if states:
        run.finding("waited out a publish already in progress: " + " -> ".join(states),
                    [run.evidence("schema", "publish_progress", " -> ".join(states))], "high")
    if not admin.is_published(module):
        pending = [_modname(p) for p in pending_rows]
        others = sorted({m for m in pending if m != module})
        pev = run.evidence("schema", "pending_changes", ", ".join(pending))
        if others:
            run.finding(f"not publishing: other staged changes would go live too "
                        f"({', '.join(others)})", [pev], "high")
            run.next("publish after the owners of those changes agree",
                     "publish is appliance-wide", 3)
            return run.result("partial")
        progress: list[str] = []
        try:
            try:
                admin.publish(progress=progress.append)
            except TypeError:  # a pyfsr without the progress callback
                admin.publish()
        except TimeoutError as exc:
            run.unknown(f"publish did not finish in time: {exc}")
            return run.result("blocked")
        if progress:
            run.evidence("schema", "publish_progress", " -> ".join(progress))
        ok = bool(admin.is_published(module))
        run.finding(f"published {module}" if ok else f"publish did not make {module} live",
                    [pev, run.evidence("module", module, f"published={ok}")], "high")
        if not ok:
            return run.result("partial")

    # 3. grant
    for role in roles or DEFAULT_ROLES:
        if role_api is None:
            run.unknown("this client cannot change roles (no roles API)")
            break
        try:
            role_api.grant_module_permissions(role, module=module, can_execute=False)
            run.finding(f"granted {role!r} read/create/update/delete on {module}",
                        [run.evidence("permission", f"{role} -> {module}", "granted")], "high")
        except Exception as exc:  # noqa: BLE001
            run.evidence("permission", f"{role} -> {module}", repr(exc)[:200], ok=False)
            run.unknown(f"could not grant {role!r}: {exc}")

    # 4. verify
    acc, code, excerpt = A.probe_read(module, client)
    rev = run.evidence("permission", f"read /api/3/{module}", f"HTTP {code}")
    if acc == "ok":
        run.finding(f"the caller can read {module}", [rev], "high")
    else:
        run.finding(f"the caller still cannot read {module}: {acc}. Fix: {A.fix_for(acc)}",
                    [rev], "high")
    record = probe_record or {"name": "provision probe"}
    cacc, ccode, cexcerpt = A.probe_create(module, record, client)
    cev = run.evidence("permission", f"create /api/3/{module}", f"HTTP {ccode}: {cexcerpt[:120]}")
    if cacc == "ok":
        run.finding(f"the caller can create {module} records (probe created and removed)",
                    [cev], "high")
    else:
        run.finding(f"the caller cannot create {module} records: {cacc} "
                    f"(HTTP {ccode}). Fix: {A.fix_for(cacc) or 'check the probe record fields'}",
                    [cev], "high")
    usable = acc == "ok" and cacc == "ok"
    if usable:
        run.next("build_playbook(...)", f"{module} is ready for automation", 1)
    return run.result("answered" if usable else "partial")

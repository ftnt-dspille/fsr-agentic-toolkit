"""build_connector: scaffold, validate and ready a FortiSOAR connector, local-first.

Composes the three layers for connector authoring:
  - scaffold a gate-clean skeleton (pyfsr.connector_dev, the SDK dev helper),
  - validate it statically (fsr_playbooks.connectors, the domain gates: info.json
    shape, dispatch coverage, imports, class structure, and code safety),
and returns findings + the next calls (fix what the gates flag, then deploy with
pyfsr). Unlike the box-reading workflows this one works on a connector DIRECTORY
and needs no live client until deploy. Re-run it after each edit: it is the
scaffold -> edit -> validate loop in one call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .result import WorkflowRun


def build_connector(directory: str, *, name: str = "",
                    scaffold: dict | None = None) -> Any:
    """directory: where the connector lives (or will be scaffolded).
    scaffold: when given and the directory has no info.json, scaffold first --
    {name?, label?, publisher?, ops?: [{operation, method?, parameters?}]}.
    """
    run = WorkflowRun("build_connector")
    path = Path(directory)

    did_scaffold = False
    if scaffold is not None and not (path / "info.json").exists():
        import os

        from pyfsr.connector_dev import scaffold_connector

        from ..verbs.tools import _safe_connector_dir
        if str(os.environ.get("FSR_MCP_READONLY", "")).strip().lower() in {"1", "true", "yes", "on"}:
            run.unknown("read-only mode (FSR_MCP_READONLY): will not scaffold files to disk")
            run.next("re-run without FSR_MCP_READONLY to scaffold, or point at an existing "
                     "connector directory to validate it", "scaffolding writes files")
            return run.result("blocked")
        path = _safe_connector_dir(str(path))  # refuse symlink/workspace escape
        spec = dict(scaffold)
        cname = spec.pop("name", None) or name or path.name
        written = scaffold_connector(cname, path, **spec)
        did_scaffold = True
        ev = run.evidence("schema", str(path),
                          f"scaffolded {cname}: {', '.join(p.name for p in written)}")
        run.finding("Scaffolded a gate-clean connector skeleton to build on; fill in "
                    "each handler's endpoint, query and body.", [ev], "high")

    if not (path / "info.json").exists():
        run.unknown(f"no connector at {path} (info.json missing) and no scaffold spec given")
        run.next("build_connector(directory=..., scaffold={'ops': [...]})",
                 "scaffold a new connector, or point at an existing directory")
        return run.result("blocked")

    from fsr_playbooks.connectors import Severity, scan, validate_connector

    issues = validate_connector(path)
    errors = [i for i in issues if i.severity == Severity.ERROR]
    warns = [i for i in issues if i.severity == Severity.WARN]
    code_issues = scan(path)
    run.data["errors"] = [{"code": i.code, "message": i.message,
                           "file": Path(i.file).name if i.file else None, "line": i.line}
                          for i in errors]
    run.data["warnings"] = [{"code": i.code, "message": i.message} for i in warns]
    run.data["code_issues"] = [{"code": f.code, "line": f.line, "message": f.message}
                               for f in code_issues]

    ev_val = run.evidence("schema", str(path),
                          f"static checks: {len(errors)} error(s), {len(warns)} warning(s), "
                          f"{len(code_issues)} code issue(s)")

    if errors or code_issues:
        for i in errors:
            where = f" ({Path(i.file).name}:{i.line})" if i.file else ""
            run.finding(f"Blocking: {i.code} {i.message}{where}", [ev_val], "high")
        for f in code_issues:
            run.finding(f"Code issue: {f.message} (operations.py:{f.line})",
                        [ev_val], "high")
        run.next("edit info.json / operations.py to resolve the issues above, then "
                 "build_connector(directory=...) again", "the connector is not yet sound")
        return run.result("partial")

    run.finding("Connector passes the static checks: info.json shape, dispatch coverage, "
                "imports and class structure are sound and the code has no security or "
                "robustness problems.",
                [ev_val], "high")
    if warns:
        run.finding(f"{len(warns)} non-blocking warning(s) remain (see data.warnings).",
                    [ev_val], "low")
    verb = "The scaffolded" if did_scaffold else "This"
    run.unknown(f"{verb} connector is statically sound but UNVERIFIED at runtime -- its "
                "handlers' real request/response behaviour is not checked here.")
    run.next("pyfsr: deploy_connector(client, directory) to package, install and healthcheck "
             "on a box (bumps the version on reupload to avoid a ghost connector)",
             "install and smoke-test the connector live", tier=3)
    return run.result("answered")

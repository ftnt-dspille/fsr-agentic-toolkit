"""build_connector: scaffold + static-validate a connector in one workflow call.

Needs the connector domain gates (fsr_playbooks.connectors) and pyfsr's
scaffold ``ops=`` support, both of which land in the next fsr_playbooks / pyfsr
release; skip cleanly until then so CI stays green, while a local run with the
worktrees on PYTHONPATH exercises it.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

fsrpb_connectors = pytest.importorskip("fsr_playbooks.connectors")
import inspect

from pyfsr.connector_dev import scaffold_connector as _scaffold

if "ops" not in inspect.signature(_scaffold).parameters:
    pytest.skip("pyfsr scaffold_connector lacks ops=; needs the newer pyfsr",
                allow_module_level=True)

from fsr_agentic_toolkit.workflow_tools import WORKFLOW_SPECS, WORKFLOW_TIERS
from fsr_agentic_toolkit.workflows.connector import build_connector


def test_scaffold_then_validate_is_answered_and_clean():
    d = Path(tempfile.mkdtemp()) / "acme-edr"
    r = build_connector(str(d), scaffold={"label": "Acme EDR", "ops": [
        {"operation": "get_device", "method": "GET",
         "parameters": [{"name": "device_id", "required": True}]}]})
    assert r.status == "answered", r.model_dump()
    assert r.data["errors"] == [] and r.data["code_issues"] == []
    assert any("deploy_connector" in n.action for n in r.next)
    # every finding cites evidence (the WorkflowResult contract)
    assert all(f.evidence for f in r.findings)


def test_broken_connector_is_partial_with_blocking_findings():
    d = Path(tempfile.mkdtemp()) / "broken"
    d.mkdir(parents=True)
    (d / "info.json").write_text(json.dumps(
        {"name": "broken", "label": "B", "version": "1.0.0", "operations": []}))  # no publisher
    (d / "operations.py").write_text("operations = {}\n")
    (d / "connector.py").write_text("x = 1\n")
    r = build_connector(str(d))
    assert r.status == "partial"
    codes = {e["code"] for e in r.data["errors"]}
    assert "INFO001" in codes  # missing publisher
    assert r.findings and any(n.action.startswith("edit") for n in r.next)


def test_missing_dir_without_scaffold_is_blocked():
    r = build_connector(str(Path(tempfile.mkdtemp()) / "nope"))
    assert r.status == "blocked"


def test_registered_as_a_workflow_tool():
    names = {s.name for s in WORKFLOW_SPECS}
    assert "build_connector" in names
    assert WORKFLOW_TIERS["build_connector"] == 1
    spec = next(s for s in WORKFLOW_SPECS if s.name == "build_connector")
    assert not spec.destructive  # scaffold writes locally; deploy is a separate call

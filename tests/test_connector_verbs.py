"""The low-level connector verbs (validate_connector, scan_connector_code,
scaffold_connector) run the domain gates / pyfsr scaffold against a directory
with no box. They are the primitives build_connector composes, marked advanced
so the workflow leads. All offline; client is unused."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fsr_playbooks.connectors")

from fsr_agentic_toolkit.verbs import tools


def test_verbs_are_registered_and_advanced():
    for n in ("validate_connector", "scan_connector_code", "scaffold_connector"):
        assert tools.REGISTRY[n].advanced is True, n
        # advertised as a building block pointing back at the workflow
        desc = tools.REGISTRY[n].to_dict()["description"]
        assert desc.startswith("[low-level building block]")
        assert "build_connector" in desc


def _write_connector(root: Path, *, verify_false: bool = False, dispatch_all: bool = True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "info.json").write_text(json.dumps({
        "name": "t_conn", "label": "T Conn", "version": "1.0.0",
        "publisher": "Test", "cs_approved": False, "cs_compatible": True,
        "category": "Utilities", "icon_small_name": "", "icon_large_name": "",
        "help_online": "", "configuration": {"fields": []},
        "operations": [{"operation": "ping", "title": "Ping", "description": "p",
                        "category": "investigation", "annotation": "ping",
                        "output_schema": {}, "enabled": True, "parameters": []}],
    }, indent=2))
    verify = "verify=False" if verify_false else "verify=True"
    disp = '{"ping": ping}' if dispatch_all else "{}"
    (root / "operations.py").write_text(
        "import requests\n\n\n"
        "def ping(config, params):\n"
        "    try:\n"
        f"        r = requests.get(config['server'], {verify})\n"
        "        r.raise_for_status()\n"
        "        return r.json()\n"
        "    except Exception as e:\n"
        "        raise ConnectorError(str(e))\n\n\n"
        f"operations = {disp}\n")
    (root / "connector.py").write_text(
        "from connectors.core.connector import Connector\n"
        "from .operations import operations\n\n\n"
        "class TConn(Connector):\n"
        "    def execute(self, config, operation, params, **kwargs):\n"
        "        return operations[operation](config, params)\n\n"
        "    def check_health(self, config):\n"
        "        return True\n")
    (root / "requirements.txt").write_text("")


def test_validate_connector_flags_missing_dispatch(tmp_path):
    good = tmp_path / "good"
    _write_connector(good, dispatch_all=True)
    assert tools._h_validate_connector(None, directory=str(good))["clean"] is True

    bad = tmp_path / "bad"
    _write_connector(bad, dispatch_all=False)  # ping not in dispatch dict
    out = tools._h_validate_connector(None, directory=str(bad))
    assert out["clean"] is False
    assert any(r["code"].startswith("OP") for r in out["errors"]), out["errors"]


def test_scan_connector_code_flags_verify_false(tmp_path):
    clean = tmp_path / "clean"
    _write_connector(clean, verify_false=False)
    assert tools._h_scan_connector_code(None, directory=str(clean))["clean"] is True

    bypass = tmp_path / "bypass"
    _write_connector(bypass, verify_false=True)
    out = tools._h_scan_connector_code(None, directory=str(bypass))
    assert out["clean"] is False
    assert any(f["code"] == "AP002" for f in out["issues"]), out["issues"]


def test_scaffold_connector_writes_a_gate_clean_skeleton(tmp_path):
    dest = tmp_path / "newconn"
    out = tools._h_scaffold_connector(None, directory=str(dest), name="newconn")
    assert out["scaffolded"] == "newconn"
    assert "info.json" in out["files"] and "operations.py" in out["files"]
    # what it writes passes the gates it ships with
    assert tools._h_validate_connector(None, directory=str(dest))["clean"] is True
    assert tools._h_scan_connector_code(None, directory=str(dest))["clean"] is True

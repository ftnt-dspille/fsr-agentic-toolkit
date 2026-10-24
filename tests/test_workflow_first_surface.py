"""The tool surface presents WORKFLOWS first and the low-level primitives they
subsume last, each carrying a lead tag, so a model takes the recommended verb
instead of hand-rolling it. Pins the design that makes this a professional
agentic toolkit (workflow-first routing, like REA's "route the target first").
All offline."""

from __future__ import annotations

import pytest

from fsr_agentic_toolkit.spec import ToolSpec, _obj
from fsr_agentic_toolkit.verbs import tools


def _noop(client, **kwargs):
    return {}


def test_workflows_are_advertised_before_ordinary_before_advanced():
    ranks = [t.surface_rank for t in tools.enabled_tools()]
    assert ranks == sorted(ranks), "surface must be ordered workflow(0) -> ordinary(1) -> advanced(2)"
    names = [t.name for t in tools.enabled_tools()]
    # every workflow precedes every advanced primitive it subsumes
    assert names.index("provision_module") < names.index("create_module")
    assert names.index("provision_module") < names.index("publish")


def test_the_seven_workflows_lead_the_surface():
    lead = [t.name for t in tools.enabled_tools() if t.workflow]
    assert set(lead) == {
        "debug_run", "verify_effect", "orient", "provision_module",
        "repair_playbook", "investigate_alert", "build_playbook",
    }
    # they occupy the front of the list, uninterrupted
    names = [t.name for t in tools.enabled_tools()]
    assert names[: len(lead)] == lead


def test_subsumed_primitives_are_marked_advanced():
    assert tools.REGISTRY["create_module"].advanced is True
    assert tools.REGISTRY["publish"].advanced is True
    # create_record stays a primary tool -- records work is its own job, not subsumed
    assert tools.REGISTRY["create_record"].advanced is False


def test_advertised_description_carries_the_lead_tag():
    prov = tools.REGISTRY["provision_module"].to_dict()["description"]
    assert prov.startswith("WORKFLOW (preferred path) -- ")
    cm = tools.REGISTRY["create_module"].to_dict()["description"]
    assert cm.startswith("[low-level building block] ")
    # a subsumed primitive points the model at the workflow
    assert "provision_module" in cm
    assert "provision_module" in tools.REGISTRY["publish"].to_dict()["description"]


def test_a_tool_cannot_be_both_workflow_and_advanced():
    with pytest.raises(ValueError):
        ToolSpec("x", "d", _obj({}), _noop, workflow=True, advanced=True)


def test_mcp_server_advertises_the_operating_guide():
    from fsr_agentic_toolkit.verbs import mcp

    assert "provision_module" in mcp.OPERATING_GUIDE
    assert "WORKFLOW" in mcp.OPERATING_GUIDE or "workflow" in mcp.OPERATING_GUIDE
    # routing guidance names orient as the starting read
    assert "orient" in mcp.OPERATING_GUIDE

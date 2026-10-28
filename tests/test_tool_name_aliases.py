"""Renamed tools keep their former names as hidden dispatch aliases, so saved
MCP calls, scripts and skills that use the old name keep working. The alias
resolves to the same spec but is never advertised (only the current name shows
in the catalog). All offline."""
from __future__ import annotations

from fsr_agentic_toolkit.verbs import tools

# former name -> current name
RENAMES = {
    "orient": "survey_box",
    "debug_run": "debug_playbook_run",
    "verify_effect": "verify_write",
    "publish": "publish_module_changes",
    "execution_context": "get_execution_context",
    "record_lifecycle": "get_record_history",
    "map_use_case": "plan_use_case",
    "audit_activities": "list_record_activity",
    "default_connector_config": "get_default_connector_config",
    "schedule_audit": "diagnose_schedule",
    "diagnose_run": "diagnose_playbook_run",
}


def test_each_old_name_resolves_to_the_renamed_spec():
    for old, new in RENAMES.items():
        assert new in tools.REGISTRY, f"current name {new} missing"
        assert old in tools.REGISTRY, f"alias {old} dropped"
        assert tools.REGISTRY[old] is tools.REGISTRY[new], f"{old} must alias {new}"
        assert tools.REGISTRY[new].name == new


def test_aliases_are_never_advertised():
    advertised = {t.name for t in tools.enabled_tools()}
    for old, new in RENAMES.items():
        assert old not in advertised, f"former name {old} leaked into the catalog"
        assert new in advertised, f"current name {new} not advertised"


def test_an_alias_dispatches_like_the_current_name():
    # Routing by an alias reaches the handler, not the UnknownTool path. Use a
    # client-free tool (plan_use_case ignores the client, reads a local store) and
    # omit its required arg: the handler raises a plain arg error, proving the
    # alias resolved -- and no appliance or network is touched.
    out = tools.dispatch(None, "map_use_case", {})
    assert isinstance(out, dict) and "error" in out
    assert out["error"].get("type") != "UnknownTool"

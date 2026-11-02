"""provision_module verify fix (offline).

The create-probe used a fixed ``{"name": ...}`` record, which fails HTTP 400
   on a module whose required field is something else (e.g. ``hostname``), so a
   usable module reported "partial". _default_probe builds the probe from the
   module's required fields.
"""

from __future__ import annotations

from fsr_agentic_toolkit.workflows import provision


def test_default_probe_fills_required_fields():
    probe = provision._default_probe([
        {"name": "hostname", "type": "text", "required": True},
        {"name": "ip_address", "type": "text"},  # not required -> excluded
        {"name": "criticality", "type": "picklist", "required": True},  # no safe value
        {"name": "count", "type": "integer", "required": True},
    ])
    assert probe["hostname"] == "provision-probe"
    assert probe["count"] == 1
    assert "ip_address" not in probe, "non-required field should not be in the probe"
    assert "criticality" not in probe, "required picklist has no safe guess"
    # The old fixed default would have been {"name": "provision probe"}.
    assert "name" not in probe


def test_default_probe_falls_back_when_no_required_fields():
    assert provision._default_probe([{"name": "note", "type": "text"}]) == {"name": "provision probe"}
    assert provision._default_probe(None) == {"name": "provision probe"}

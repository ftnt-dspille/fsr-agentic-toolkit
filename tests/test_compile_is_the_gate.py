"""compile_playbook_yaml is the one offline gate: structure AND grounded checks.

An agent that reaches compile_playbook_yaml must land on the real answer without
having to know a second tool name. So a reference that compiles clean but would
render empty at run time (a step-output path the op does not produce) makes the
gate fail, surfacing the grounded required_fix. The compiler alone cannot see
this; the grounded oracle can.
"""
from __future__ import annotations

from fsr_agentic_toolkit.verbs import tools


class _Ok:
    ok = True
    fsr_json = {"data": [{"name": "C", "workflows": [{"name": "P"}]}]}


class _Bad:
    ok = False
    fsr_json = {}
    errors = []


def test_runtime_reference_defect_fails_the_gate(monkeypatch):
    monkeypatch.setattr(tools, "_compile_playbook_yaml", lambda _y: _Ok())
    monkeypatch.setattr(tools, "_grounded_required_fixes",
                        lambda _y: [{"code": "missing_field_on_step_output",
                                     "message": "Get_Macros produces a LIST; .data.value is empty"}])
    out = tools._h_compile_playbook_yaml(None, yaml_text="x")
    assert out["ok"] is False, out            # red without the gate: compile alone was ok
    assert out["required_fixes"], out
    assert "run time" in out["note"], out["note"]


def test_clean_structure_and_grounded_is_safe_to_publish(monkeypatch):
    monkeypatch.setattr(tools, "_compile_playbook_yaml", lambda _y: _Ok())
    monkeypatch.setattr(tools, "_grounded_required_fixes", lambda _y: [])
    out = tools._h_compile_playbook_yaml(None, yaml_text="x")
    assert out["ok"] is True and out["playbooks"] == ["P"]
    assert "publish_playbook" in out["note"]


def test_structure_error_returns_before_the_grounded_check(monkeypatch):
    monkeypatch.setattr(tools, "_compile_playbook_yaml", lambda _y: _Bad())
    called = {"n": 0}
    monkeypatch.setattr(tools, "_grounded_required_fixes",
                        lambda _y: called.__setitem__("n", called["n"] + 1))
    out = tools._h_compile_playbook_yaml(None, yaml_text="x")
    assert out["ok"] is False and called["n"] == 0  # cannot gate uncompilable YAML


def test_gate_unavailable_is_reported_not_claimed_clean(monkeypatch):
    monkeypatch.setattr(tools, "_compile_playbook_yaml", lambda _y: _Ok())
    monkeypatch.setattr(tools, "_grounded_required_fixes", lambda _y: None)
    out = tools._h_compile_playbook_yaml(None, yaml_text="x")
    assert out["ok"] is True
    assert "unavailable" in out["note"] or "STRUCTURE only" in out["note"], out["note"]

"""A clean compile must NOT steer straight to publish.

compile_playbook_yaml is a structure check only; it does not catch a step-output
reference that compiles clean but renders empty at run time. Its success note
used to say "Call publish_playbook ... to push it", which invited the agent to
skip the grounded gate (observed in a live runtime-dependency drive). The note
must instead send the agent through build_playbook first.
"""
from __future__ import annotations

from fsr_agentic_toolkit.verbs import tools


class _Result:
    ok = True
    fsr_json = {"data": [{"name": "C", "workflows": [{"name": "P"}]}]}


def test_clean_compile_note_points_to_the_gate_not_publish(monkeypatch):
    monkeypatch.setattr(tools, "_compile_playbook_yaml", lambda _y: _Result())
    out = tools._h_compile_playbook_yaml(None, yaml_text="collection: C\n")
    assert out["ok"] is True
    note = out["note"]
    assert "build_playbook" in note, note
    assert "Call publish_playbook" not in note, note
    # it must name the limitation that justifies the extra gate
    assert "run time" in note or "runtime" in note, note

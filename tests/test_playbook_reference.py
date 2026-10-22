"""playbook_yaml_reference: the authoring reference is reachable without a shell."""
from __future__ import annotations

from pathlib import Path

import pytest

from fsr_agentic_toolkit.verbs import tools


def _call(args):
    return tools.dispatch(None, "playbook_yaml_reference", args)


def test_overview_has_shape_and_topics():
    out = _call({})
    assert "Top-level shape" in out["reference"]
    assert "Routing" in out["topics"]
    assert tools.REGISTRY["playbook_yaml_reference"].tier == 1


@pytest.mark.parametrize("topic", ["create_record", "manual_input", "for_each", "Routing", "workflow_reference"])
def test_topic_returns_its_section(topic):
    out = _call({"topic": topic})
    assert "error" not in out, out
    assert "reference" in out
    assert topic.lower() in out["heading"].lower() or topic.lower() in out["reference"].lower()


def _all_parts(topic):
    out = _call({"topic": topic})
    text = [out["reference"]]
    while "next" in out:
        out = _call(out["next"])
        assert "error" not in out, out
        text.append(out["reference"])
    return "\n".join(text)


def test_subsections_come_with_their_parent():
    assert "on_conflict" in _all_parts("create_record")


def test_parts_fit_the_output_cap():
    import json

    for topic in ["", "create_record", "Routing", "Variables and Jinja"]:
        out = _call({"topic": topic} if topic else {})
        assert "_truncated" not in out, topic
        assert len(json.dumps(out)) <= 4000, topic


def test_unknown_topic_lists_topics():
    out = _call({"topic": "no-such-thing"})
    assert out["error"]["type"] == "ValueError"
    assert "Routing" in out["error"]["message"]


_FW_DOC = Path.home() / "PycharmProjects/fsr-playbook-framework/docs/AUTHORING.md"


@pytest.mark.skipif(not _FW_DOC.exists(), reason="framework checkout not present")
def test_bundled_reference_matches_framework_doc():
    from importlib.resources import files

    bundled = (files("fsr_agentic_toolkit") / "reference" / "AUTHORING.md").read_text(encoding="utf-8")
    assert bundled == _FW_DOC.read_text(encoding="utf-8"), "re-copy docs/AUTHORING.md from fsr-playbook-framework"

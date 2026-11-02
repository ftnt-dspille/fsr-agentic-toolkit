"""The packaged agent skills install where coding agents read them."""
from __future__ import annotations

from fsr_agentic_toolkit import skills_cmd as S

EXPECTED = {"alert-investigation", "box-onboarding", "connector-building", "connector-secrets",
            "debug-and-repair", "playbook-authoring", "prove-the-work"}


def test_every_skill_ships_with_a_description():
    found = S.bundled()
    assert set(found) == EXPECTED
    for name, src in found.items():
        assert S._description(src / "SKILL.md"), name


def test_skills_name_only_tools_the_toolkit_has():
    import re

    from fsr_agentic_toolkit.verbs.tools import REGISTRY

    # Python names the connector skill shows in code, not tool calls.
    code_names = {"execute", "check_health", "deploy_connector", "quote", "print"}
    for name, src in S.bundled().items():
        text = (src / "SKILL.md").read_text()
        for call in re.findall(r"`([a-z_]+)\(", text):
            assert call in REGISTRY or call in code_names, f"{name} names {call}(), which is not a toolkit tool"


def test_install_copies_and_keeps_local_edits(tmp_path):
    assert all(line.startswith("installed") for line in S.install(tmp_path))
    assert all(line.startswith("unchanged") for line in S.install(tmp_path))
    edited = tmp_path / "prove-the-work" / "SKILL.md"
    edited.write_text("team notes")
    lines = S.install(tmp_path)
    assert any(line.startswith("kept       prove-the-work") for line in lines)
    assert edited.read_text() == "team notes"
    S.install(tmp_path, force=True)
    assert edited.read_text() != "team notes"


def test_agents_md_block_is_added_once_and_refreshed(tmp_path):
    md = tmp_path / "AGENTS.md"
    md.write_text("# My project\n\nKeep this.\n")
    S.write_agents_md(md, tmp_path / "skills")
    S.write_agents_md(md, tmp_path / "skills")
    text = md.read_text()
    assert text.count(S._BEGIN) == 1 and "Keep this." in text
    assert "playbook-authoring" in text

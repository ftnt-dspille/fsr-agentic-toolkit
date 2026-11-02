"""``fsr-agentic-toolkit skills``: put the toolkit's agent skills where an agent reads them.

The skills ship inside the package (``fsr_agentic_toolkit/skills/<name>/SKILL.md``).
Claude Code loads a skill folder from ``.claude/skills``; agents that read
``AGENTS.md`` (Codex, OpenCode) get a marked block that lists each skill and
points at its installed file.
"""
from __future__ import annotations

import argparse
import re
import shutil
from importlib import resources
from pathlib import Path

_BEGIN = "<!-- fsr-agentic-toolkit skills: begin -->"
_END = "<!-- fsr-agentic-toolkit skills: end -->"


def bundled() -> dict[str, Path]:
    """Skill name -> its packaged folder."""
    root = Path(str(resources.files("fsr_agentic_toolkit") / "skills"))
    return {p.name: p for p in sorted(root.iterdir()) if (p / "SKILL.md").is_file()}


def _description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    m = re.search(r"^description:\s*>?-?\s*\n((?:\s+.+\n)+)", text, re.MULTILINE)
    if m:
        return " ".join(line.strip() for line in m.group(1).splitlines())
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def install(dest: Path, *, force: bool = False) -> list[str]:
    """Copy every bundled skill into ``dest``; returns one status line per skill.

    A skill folder that exists and differs is left alone unless ``force``, so a
    team's local edits are never overwritten silently.
    """
    out = []
    dest.mkdir(parents=True, exist_ok=True)
    for name, src in bundled().items():
        target = dest / name
        new = (src / "SKILL.md").read_text(encoding="utf-8")
        if target.exists():
            current = (target / "SKILL.md").read_text(encoding="utf-8") if (target / "SKILL.md").exists() else ""
            if current == new:
                out.append(f"unchanged  {name}")
                continue
            if not force:
                out.append(f"kept       {name} (differs from the bundled copy; --force to replace)")
                continue
            shutil.rmtree(target)
        shutil.copytree(src, target)
        out.append(f"installed  {name}")
    return out


def agents_md_block(skills_dir: Path) -> str:
    lines = [_BEGIN, "## FortiSOAR skills", "",
             "Before a FortiSOAR task, read the matching skill file and follow it:", ""]
    for name, src in bundled().items():
        lines.append(f"- **{name}** (`{skills_dir / name / 'SKILL.md'}`): {_description(src / 'SKILL.md')}")
    lines.append(_END)
    return "\n".join(lines) + "\n"


def write_agents_md(path: Path, skills_dir: Path) -> str:
    """Add or refresh the marked block in ``path``; text outside it is untouched."""
    block = agents_md_block(skills_dir)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if _BEGIN in text and _END in text:
        start, end = text.index(_BEGIN), text.index(_END) + len(_END)
        text = text[:start] + block.rstrip("\n") + text[end:]
        verb = "updated"
    else:
        text = (text.rstrip("\n") + "\n\n" if text else "") + block
        verb = "added"
    path.write_text(text, encoding="utf-8")
    return f"{verb} the skills block in {path}"


def cmd_skills(args: argparse.Namespace) -> int:
    if not args.install and not args.agents_md:
        for name, src in bundled().items():
            print(f"{name:22} {_description(src / 'SKILL.md')}")
        return 0
    dest = Path(args.dir).expanduser()
    if args.install:
        for line in install(dest, force=args.force):
            print(line)
    if args.agents_md:
        print(write_agents_md(Path(args.agents_md).expanduser(), dest))
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("skills", help="list the agent skills, or install them for your coding agent")
    p.add_argument("--install", action="store_true", help="copy the skills into --dir")
    p.add_argument("--dir", default=".claude/skills", help="skills folder (default .claude/skills, Claude Code)")
    p.add_argument("--force", action="store_true", help="replace installed skills that differ")
    p.add_argument("--agents-md", metavar="PATH", help="add/refresh a skills block in this AGENTS.md (Codex, OpenCode)")
    p.set_defaults(func=cmd_skills)

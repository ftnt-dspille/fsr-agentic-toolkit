"""Runtime traps in playbook YAML that compile clean and fail (or lie) on the box.

The compiler checks structure; these are behaviours of the running engine,
each confirmed on a live appliance:

- a code_snippet step's output is what its code prints; a snippet that never
  prints hands the next step nothing
- the snippet sandbox rejects some identifier NAMES (``all``, ``map``, ``set``,
  ``isinstance`` ...), not only calls to them, and fails the step
- a find_record step's result IS the row list; ``hydra:member`` on it is always
  empty, so logic built on it silently detects nothing
- a literal ``/api/3/picklists/<uuid>`` in a record write is refused at run time;
  resolve picklists with the runtime ``picklist`` filter instead
"""
from __future__ import annotations

import re
from typing import Any

import yaml

_BLOCKED_NAMES = ("all", "map", "set", "isinstance", "seq")
_STRINGS = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|#[^\n]*")
_NAME_RE = {n: re.compile(rf"(?<![\w.]){n}\b") for n in _BLOCKED_NAMES}
_PICKLIST_LITERAL = re.compile(r"/api/3/picklists/[0-9a-f-]{36}")
_WRITE_TYPES = {"create_record", "update_record", "upsert_record"}


def _steps(doc: Any):
    if not isinstance(doc, dict):
        return
    for pb in doc.get("playbooks") or []:
        if not isinstance(pb, dict):
            continue
        for step in pb.get("steps") or []:
            if isinstance(step, dict):
                yield pb.get("name", "?"), step


def lint_yaml(yaml_text: str) -> list[dict[str, str]]:
    """Each trap found: {"step", "code", "message", "fix"}."""
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return []  # the compiler reports YAML syntax
    issues: list[dict[str, str]] = []
    finders = set()
    for _pb, step in _steps(doc):
        if step.get("type") == "find_record":
            finders.add(str(step.get("name")))
    for _pb, step in _steps(doc):
        name, stype = str(step.get("name", "?")), step.get("type")
        if stype == "code_snippet":
            code = str(step.get("code") or (step.get("arguments") or {}).get("code") or "")
            if code and "print(" not in code and "raise" not in code:
                issues.append({"step": name, "code": "snippet-no-print",
                               "message": "the step's output is what its code prints, and it prints nothing",
                               "fix": "print() the value the next step needs (print(dict) keeps the type)"})
            bare = _STRINGS.sub("", code)  # names inside strings and comments are fine
            for n, rx in _NAME_RE.items():
                if rx.search(bare):
                    issues.append({"step": name, "code": "snippet-blocked-name",
                                   "message": f"the snippet sandbox rejects the name {n!r} and fails the step",
                                   "fix": f"rename or avoid {n!r} (use `or []` / `or {{}}` instead of type checks)"})
        text = yaml.safe_dump(step, sort_keys=False)
        for f in finders:
            if re.search(rf"steps\.{re.escape(f.replace(' ', '_'))}\b[^}}]*hydra:member", text):
                issues.append({"step": name, "code": "find-record-hydra",
                               "message": f"{f!r} returns the row list itself; hydra:member on it is always empty",
                               "fix": f"use vars.steps.{f.replace(' ', '_')} directly as the list"})
        if stype in _WRITE_TYPES and _PICKLIST_LITERAL.search(text):
            issues.append({"step": name, "code": "picklist-literal-iri",
                           "message": "a literal picklist IRI in a record write is refused at run time",
                           "fix": "use {{ \"<Picklist>\" | picklist(\"<Value>\", \"@id\") }}"})
    return issues

---
name: playbook-authoring
description: >-
  Write, check and publish a FortiSOAR playbook with the fsr-agentic-toolkit.
  Use when asked to build a playbook from a goal, change one, or automate a
  task on records (on-create/on-update triggers, scheduled or manual runs,
  Python code steps, sweeps over many records).
---

# Authoring a FortiSOAR playbook

## The loop

1. `survey_box(goal=..., modules=[...])`: the real field names, picklist values and
   your access. Never guess a field name.
2. `playbook_yaml_reference(topic=<step type>)` for any step you have not written before.
3. Write the YAML, then `compile_playbook_yaml`. Fix every `errors`, `required_fixes`
   and `runtime_traps` entry and compile again until `ok` is true.
4. `publish_playbook` (or `run_playbook_yaml` for a one-off test run that cleans up).
5. Prove it: create a test record that should trigger it, `wait_for_playbook_run`,
   then read the record back. The read-back is the proof, not the run status.

## Traps that compile clean and fail on the box

The compile check flags these; know why so you fix them right:

- A `code_snippet` step's output is what its code **prints**. Assigning a variable
  hands the next step nothing. `print(dict)` keeps the type.
- The snippet sandbox rejects some names outright: `all`, `map`, `set`, `isinstance`,
  `seq`. Use `or []` / `or {}` instead of type checks.
- A `find_record` step's result **is** the row list. `hydra:member` on it is always
  empty, so a sweep built on it runs clean and changes nothing.
- Never write a picklist as a fixed `/api/3/picklists/<uuid>` in a record write. Use
  `{{ "AlertStatus" | picklist("Investigating", "@id") }}`.
- Dates are epoch seconds on the box. The record tools convert ISO for you; inside a
  playbook, convert explicitly.

## Scope every trigger

Trigger conditions must be as narrow as the goal: if the goal says "Critical alerts
named X", the trigger checks both the name and the severity. Test the negative case
too (a record that must be left alone) and show both results.

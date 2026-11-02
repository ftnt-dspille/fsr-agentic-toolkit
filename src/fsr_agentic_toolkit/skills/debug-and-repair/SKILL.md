---
name: debug-and-repair
description: >-
  Find why a FortiSOAR playbook run failed and fix it with the toolkit's own
  repair, then prove the fix with a new run. Use when a playbook run failed,
  finished with an error, or did not have its effect.
---

# Debugging and repairing a playbook run

1. `debug_playbook_run(playbook_or_id=<name or run id>, yaml_text=<the YAML if you have it>)`.
   Read its findings: the failing step, the likely root cause, and the evidence.
2. If it attached `data.fixes`, call `repair_playbook(follow_up=<the debug result id>, yaml_text=...)`.
   It applies the exact edit and re-validates. **Do not hand-edit** what a structured
   fix covers; if repair says it needs a hand edit, say so and make only that edit.
3. Republish (`publish_playbook`) and trigger a new run on a fresh test record.
4. `wait_for_playbook_run`, then confirm the run finished **and** the record changed.
   A fix is proven only by a new run that succeeds and has its effect.

Rules:

- Quote the run's own error; do not paraphrase a guess as the cause.
- `diagnose_playbook_run` compares defined steps with what ran; use it when a run
  "finished" but skipped steps.
- If `debug_playbook_run` names no cause, report what it ruled out and stop: do not
  invent one.

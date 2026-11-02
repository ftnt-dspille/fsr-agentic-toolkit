---
name: alert-investigation
description: >-
  Investigate a FortiSOAR alert like a SOC analyst and write the findings back
  to the record. Use when asked to investigate, triage or assess an alert, or
  to compare your verdict with FortiAI's.
---

# Investigating an alert

1. `investigate_alert(ref)`: the alert, its indicators and the enrichment actions that
   fit them on this box.
2. Read the record's history (`get_record_history`) and related records.
3. Enrich with configured connectors only. If none fits, state the gap.
4. Search for other alerts with the same host, user or destination.
5. Verdict: disposition, confidence, and for each claim the tool result behind it.
   Keep evidence and inference apart.
6. Write back with `update_record`: a structured summary (verdict, evidence, gaps,
   next steps) and the severity change if warranted. Never close the alert unless asked.

Comparing with FortiAI: `run_fortiai_investigation(ref, wait=true)`, or
`get_investigation_result(task_id=<investigation_task_id>, wait=true)` if it is already
running. Compare only once `finished` is not false.

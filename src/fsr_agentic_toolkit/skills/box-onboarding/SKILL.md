---
name: box-onboarding
description: >-
  Map an unfamiliar FortiSOAR box, explain a record's history, or compare two
  boxes. Use for "what is this box", "who changed this record", and
  "what differs between staging and production".
---

# Orienting on a box

## Map the box (read-only)

`survey_box` first: version, FortiAI state, registered MCP servers, configured
connectors. Then `healthcheck_connector` on each configured connector,
`list_modules` for custom modules, and report what looks broken with the
result that shows it. Nothing here needs an approval; if you are asked to approve a
change during a survey, something is wrong.

## Record forensics

`get_record_history(module, ref)` merges audit rows and playbook runs;
`list_record_activity` adds comments and links. Build the timeline in order:
when, which fields, and who (a person, an API user, or a playbook by name). Mark
any change you cannot attribute instead of guessing.

## Two boxes

`list_instances` names the configured boxes; every tool takes `instance`. Compare
version, connectors and versions, playbook collections, and custom modules, read-only.
Call out the differences that would break promoted content: a connector or version
missing on the target, a module field a playbook writes that the target lacks.

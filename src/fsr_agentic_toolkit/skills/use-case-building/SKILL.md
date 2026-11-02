---
name: use-case-building
description: >-
  Turn a plain-language FortiSOAR use case ("track lost laptops and raise an
  alert", "reconcile assets against the CMDB") into a working build: plan,
  module, connectors, playbook, and a test that proves it. Use when asked to
  stand up, automate or build a use case end to end.
---

# Building a use case end to end

Work in this order. Each step's result feeds the next; do not skip ahead.

1. **Plan.** `plan_use_case(use_case=<the user's sentence>)`. Read the archetype, its
   confidence and the `pending` parameters. Ask the user only for pending values you
   cannot read from the box; restate the plan in one short list before building.
2. **Survey.** `survey_box(goal=..., modules=[...])`: which modules, fields, picklists
   and connectors already exist. Reuse an existing module or connector over creating
   a new one.
3. **Module.** `provision_module(...)` for any new module. It creates, publishes and
   checks the caller can read and create. Never hand-roll create_module + publish.
   Publishing takes minutes; let it finish.
4. **Connectors.** For each connector the plan needs: installed and healthy
   (`healthcheck_connector`)? If not, `setup_connector` with secrets by reference
   (see the connector-secrets skill).
5. **Playbook.** Follow the playbook-authoring skill: write the YAML against the
   module's real fields, `build_playbook(goal, yaml_text, modules)` until it is ready,
   then `publish_playbook`.
6. **Prove.** Create one realistic test record, `wait_for_playbook_run`, and read back
   both the record and whatever the playbook made (an alert, an update). Show the
   user the evidence, including a negative case if the trigger has a condition.

Report at the end: what was built (module, fields, playbook, connectors), the test
and its proof, and what is still manual or assumed. Name test records clearly so they
are easy to delete, and offer to delete them.

# Operating guide for agents

`fsr-agentic-toolkit` lets a coding agent drive FortiSOAR through the REST API.
It gives you **evidence-backed workflows** on top of the raw operations. The same
guidance is advertised to your client as the MCP server's `instructions` and
printed by `fsr-agentic-toolkit init`.

## Route your goal to a workflow first

Workflows are the recommended path and are listed first in the tool catalog. Reach
for a low-level tool only when no workflow fits.

| Your goal | Start with |
| --- | --- |
| Understand a box, or a goal's modules / access / connectors | `survey_box` |
| Create a usable custom module | `provision_module` |
| A playbook run misbehaved | `debug_playbook_run` → `repair_playbook` (pass its result id) |
| Author a playbook | `build_playbook` → `publish_playbook` → `verify_write` |
| Build or fix a connector | `build_connector` (scaffold + validate; edit, re-run), then pyfsr `deploy_connector` |
| Confirm a write actually landed | `verify_write` |
| Gather an alert's evidence | `investigate_alert` |

**Do not hand-roll a workflow from primitives.** For example, creating a module is
`provision_module` - not `create_module` + `publish_module_changes` + a role grant + a probe
write. The workflow handles the post-publish settle, the grant and the read-back,
and it never makes you sleep or poll. A module left staged-but-unpublished, or
published without a role grant, is not usable; that is the trap the workflow exists
to remove.

## How to work

- **Summary-first.** Start from the smallest useful read (`survey_box`) and expand only
  when the result leaves a concrete gap. Do not repeat an identical call.
- **Trust the workflow's waiting.** Right after a schema publish the box rejects
  record writes for a few seconds; the workflow waits that out. Never add your own
  `sleep`/poll around a publish.
- **Cite evidence.** Results carry `findings` and `evidence`; separate what was
  verified from what was assumed, and name the evidence for a claim.
- **Confirm high-impact calls.** Destructive or privileged tools (`provision_module`,
  `publish_module_changes`, deletes, `create_user`, `install_connector`) need `confirm: true`. Tell
  the user exactly what will change before you set it.

## The low-level surface

The primitives (`create_module`, `publish_module_changes`, `create_record`, record CRUD, connector
config, schedules, and the connector-authoring verbs `scaffold_connector` /
`validate_connector` / `scan_connector_code`, …) are still available and are listed
after the workflows, tagged `[low-level building block]`. Use them for the cases a
workflow does not cover - or when you deliberately need one step of a workflow on
its own (e.g. re-running `validate_connector` after a single edit).

## Safety knobs (set in the MCP server's `env`)

- `FSR_MCP_READONLY=1` - hide and refuse every mutating tool.
- `FSR_MCP_ALLOW_TOOLS` / `FSR_MCP_DENY_TOOLS` - comma-separated names or globs to
  narrow the surface.

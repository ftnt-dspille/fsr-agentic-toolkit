# fsr-agentic-toolkit

Workflows and tools that make an AI agent competent on FortiSOAR. One registry
and one safety gate, served three ways: an MCP server, a CLI, and a Python API.

## Where it sits

```
pyfsr               SDK: typed REST client, auth, instances. No agent code.
fsr_playbooks       Domain: playbook YAML, validation, compile, run analysis.
fsr-agentic-toolkit Agent layer: verbs + seven workflows, MCP server, CLI.   <- this repo
products            SOC assistant connector, widgets, workshops.
```

Each layer depends only on the ones above it. The SOC assistant connector can
register the workflows into its own dispatch with
`fsr_agentic_toolkit.workflow_tools.register_into_framework()`. They stay
hidden from its model until the host opts in.

## The seven workflows

| Workflow | Tier | What it returns |
|---|---|---|
| `orient` | read | Module fields and picklists, read access (and the fix if access is broken), matching connectors |
| `debug_run` | read | Why a run failed: the failing step, its error, and step args that don't render |
| `repair_playbook` | read | Mechanical fixes applied to a draft and re-validated; the rest listed |
| `build_playbook` | read | Facts to draft from, or a gated draft plus the next calls |
| `verify_effect` | read | What actually changed on a record versus what was expected |
| `investigate_alert` | read | Alert evidence, indicators, and the enrichment lookups available |
| `provision_module` | write, confirm | Create, publish, grant roles, then prove read and create work |

Each one returns a `WorkflowResult`: findings with evidence ids, unknowns, next
steps with their tier, and an id that a follow-up workflow can take.
Use cases compose them. A custom module plus a playbook that acts on it is
`provision_module`, then `build_playbook`, then `verify_effect`.

## Safety

Every call goes through one gate. Writes are tier 2. Destructive tools are
tier 3 and need `confirm: true`. Environment policy:

- `FSR_MCP_READONLY=1` drops every write.
- `FSR_MCP_ALLOW_TOOLS` / `FSR_MCP_DENY_TOOLS` take comma-separated names.

## Install and use

```
pip install fsr-agentic-toolkit
fsr-agentic-toolkit init --url fortisoar.example.com --api-key <key>
fsr-agentic-toolkit tools
fsr-agentic-toolkit orient --modules alerts --json
fsr-agentic-toolkit debug-run --playbook-or-id "Close Alert"
fsr-agentic-toolkit call search_records '{"module": "alerts", "limit": 5}'
fsr-agentic-toolkit mcp        # MCP server on stdio
```

The box comes from `FSR_*` environment variables or `--instance <alias>` from
`~/.pyfsr/instances.toml`. An explicit `FSR_BASE_URL` wins over the default
alias.

## Development

```
pip install -e . && pytest
```

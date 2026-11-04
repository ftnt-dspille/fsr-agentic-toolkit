# fsr-agentic-toolkit

Workflows and tools that make an AI agent competent on FortiSOAR. One registry
and one safety gate, served three ways: an MCP server, a CLI, and a Python API.

## Where it sits

```
pyfsr               SDK: typed REST client, auth, instances. No agent code.
fsr_playbooks       Domain: playbook YAML, validation, compile, run analysis.
fsr-agentic-toolkit Agent layer: verbs + eleven workflows, MCP server, CLI.   <- this repo
products            SOC assistant connector, widgets, workshops.
```

Each layer depends only on the ones above it. The SOC assistant connector can
register the workflows into its own dispatch with
`fsr_agentic_toolkit.workflow_tools.register_into_framework()`. They stay
hidden from its model until the host opts in.

## The eleven workflows

| Workflow | Tier | What it returns |
|---|---|---|
| `survey_box` | read | Module fields and picklists, read access (and the fix if access is broken), matching connectors |
| `debug_playbook_run` | read | Why a run failed: the failing step, its error, and step args that don't render |
| `repair_playbook` | read | Mechanical fixes applied to a playbook's YAML and re-validated; the rest listed |
| `build_playbook` | read | Facts to write the playbook from, or a checked playbook plus the next calls |
| `build_connector` | read | Scaffold and statically validate a connector directory; blocking issues plus the next call (deploy is a separate pyfsr call) |
| `verify_write` | read | What actually changed on a record versus what was expected |
| `investigate_alert` | read | Alert evidence, indicators, and the enrichment lookups available |
| `run_playbook_yaml` | write | Gate, publish, trigger, wait and read back a playbook from its YAML in one call; a failed run returns with its diagnosis; `cleanup=true` removes only what the call created |
| `setup_connector` | write | Install, configure and healthcheck a connector. Secrets are passed as references (`{"env": "FSR_SECRET_<CONNECTOR>__<FIELD>"}`, usable only for that connector), never as values, and the result never contains one. `install=true` also needs `confirm_install=true` |
| `provision_module` | write, confirm | Create, publish, grant roles, then prove read and create work |

Each one returns a `WorkflowResult`: findings with evidence ids, unknowns, next
steps with their tier, and an id that a follow-up workflow can take.
Use cases compose them. A custom module plus a playbook that acts on it is
`provision_module`, then `build_playbook`, then `verify_write`.

## API only

The toolkit talks to FortiSOAR through its REST API and nothing else. It has
no SSH, shell, log or database tools. Appliance shell tools are a human CLI:
`pyfsr appliance ...`.

## Safety

Every call goes through one gate. Writes are tier 2. Destructive tools are
tier 3 and need `confirm: true`. Environment policy:

- `FSR_MCP_READONLY=1` drops every write.
- `FSR_MCP_ALLOW_TOOLS` / `FSR_MCP_DENY_TOOLS` take comma-separated names.

### Approving writes

Approval is the MCP client's job, so the server does not prompt. Coding agents already ask the
user before a tool runs, and they decide what to ask about from the standard annotations the
server advertises:

1. **Tool hints.** Every tool is advertised with `readOnlyHint` / `destructiveHint`, derived
   from its tier in the registry. A client uses them to decide what may run without asking.
2. **`confirm: true`.** Destructive tools also refuse to run without it, so a call that was
   never meant to be destructive cannot become one by accident.
3. **Claude Code rules.** `fsr-agentic-toolkit permissions` prints (or `--write PATH` merges)
   `allow` rules for every read and `ask` rules for every write, generated from the tool
   registry, so the client asks on exactly the tools that change the box. A write tool added
   later is on neither list, so it prompts by default.

## Install and use

```
pip install fsr-agentic-toolkit
fsr-agentic-toolkit init --url fortisoar.example.com --api-key <key>
fsr-agentic-toolkit tools
fsr-agentic-toolkit survey-box --modules alerts --json
fsr-agentic-toolkit debug-playbook-run --playbook-or-id "Close Alert"
fsr-agentic-toolkit call search_records '{"module": "alerts", "limit": 5}'
fsr-agentic-toolkit mcp        # MCP server on stdio
```

## Agent skills

The toolkit ships skills that teach a coding agent how to use it well: playbook
authoring, debug and repair, proving the work, connector secrets, alert
investigation, box onboarding, use-case building, and connector building.

```
fsr-agentic-toolkit skills                                  # list them
fsr-agentic-toolkit skills --install                        # Claude Code: .claude/skills
fsr-agentic-toolkit skills --install --agents-md AGENTS.md  # Codex / OpenCode read AGENTS.md
```

An installed skill you have edited is kept unless you pass `--force`.

The box comes from `FSR_*` environment variables or `--instance <alias>` from
`~/.pyfsr/instances.toml`. An explicit `FSR_BASE_URL` wins over the default
alias.

## Development

One-command setup and the test loop CI runs (Python 3.11+):

```
make install    # editable install + test/lint deps into .venv
make test       # the full suite (what CI runs)
make lint       # ruff (also enforced in CI)
```

Or by hand, matching CI exactly:

```
pip install -e ".[test]"
python -m pytest -q
```

Note the `[test]` extra: `pip install -e .` alone does not pull in pytest.

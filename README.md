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
- `FSR_MCP_APPROVAL=auto|require|off` (default `auto`) puts every write to the **user** before
  it runs.

### Approving writes, in any coding agent

Coding agents do not share a permissions file format, but they all speak MCP, so the
protection lives in the server:

1. **Tool hints.** Every tool is advertised with `readOnlyHint` / `destructiveHint`, the
   standard annotations a client uses to decide what may run without asking.
2. **Approval prompt.** Before a write, the server asks the user through the client's own UI
   (MCP elicitation): the target box, the tool, and the arguments with anything
   credential-shaped masked. Anything but an explicit yes does nothing and tells the model
   not to retry. Reads never prompt, and a call the policy would refuse anyway is refused
   without bothering the user.

   `auto` asks when the client supports elicitation and otherwise runs the write, leaving it
   to the client's own tool prompts. `require` refuses the write when the client cannot be
   asked. `off` never asks.
3. **Claude Code rules.** `fsr-agentic-toolkit permissions` prints (or `--write PATH` merges)
   `allow` rules for every read and `ask` rules for every write, generated from the tool
   registry. Other agents rely on layers 1 and 2.

`FSR_MCP_APPROVAL_TIMEOUT` (seconds, default 300) bounds the wait: a client that accepts the
request but never answers (a headless run) gets a refusal, not a hang.

MCP has two protocol eras and the server speaks both. A client on the older one is sent the
approval request directly. On the 2026-07-28 protocol over stdio a server may not send requests
at all, so the server answers the call with an input-required result carrying the approval form;
the client asks the user and retries the same call with the answer, which is honoured only when
it matches that exact tool and arguments.

Measured with the real clients (each driven through its interactive UI with a person's
keystrokes, and headless), against a stub box:

| Client | Declares elicitation | Interactive approve / decline | Headless |
|---|---|---|---|
| Claude Code 2.1.312 | yes | prompt appears ("MCP server requests your input"), tick the box and Accept; works | declines, nothing written |
| Codex 0.165.0 | yes | prompt appears (`1. True / 2. False`); works | cancels, nothing written; also gates the write itself on the tool's hint |
| OpenCode 2.2.0 | yes | prompt appears (`1. Yes / 2. No`, No by default); works | never answers, so the timeout refuses it |

Gemini CLI, Cursor and Copilot were not available to test. A client that does not declare
elicitation falls back to `auto` (the write runs under that client's own tool prompts) or, with
`require`, is refused.

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

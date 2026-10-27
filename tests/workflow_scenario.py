"""Does a workflow-requiring task get done better when the toolkit workflows are offered?

An earlier screen showed no regression with the agentic toolkit's evidence-backed
workflows OFFERED (debug_run, repair_playbook, verify_effect, ...) but also no
proof of benefit: no fixture needed one and the model never called one. This is
the fixture that does need one.

Scenario (box-free, deterministic world, no paid model)
-------------------------------------------------------
A playbook ("Refresh Latest Alert Details") failed: its second step builds
``/api/3/alerts/{{ ...['@id'] }}`` from a value that is already a full IRI, so the
call goes to a doubled URL and 404s. The analyst asks the assistant to find out
why, fix it, run it again, and confirm the alert record was actually refreshed.
Success needs a chain whose later links depend on earlier evidence:

    diagnose (run id + failing step + root cause)
      -> fix the exact argument  -> push -> re-run
      -> read the RECORD back (the effect, not the run status)
      -> final answer that cites the run, the step and the cause.

The world is a small simulated box (:class:`Box`) behind the same tool NAMES the
production assistant advertises (``why_did_playbook_fail``, ``edit_playbook``,
``run_playbook``, ``get_record``, ...). The only difference between arms is the
tool list: with the toolkit registered, the workflows are withheld
(``hidden``), offered (``offered``), or offered with a one-line system-prompt
mention (``hinted``). The three workflow tools that run are the REAL toolkit
code, fed by the same simulated box; the bare tools are simulations of the
framework tools, so fidelity of the bare arm to production is the caveat.

Grading is from box state and the call log, not from the transcript's claims:

  fix_pushed   the stored playbook now renders a valid IRI for the failing arg
  effect       the re-run finished AND the alert record has the refreshed value
  read_back    the record (or verify_effect) was read AFTER the last run
  cites        the final answer names the run id, the failing step and the cause

A scenario passes only when all four hold.
"""
from __future__ import annotations

import json
import os
import re
import uuid as _uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jinja2
import yaml

PLAYBOOK = "Refresh Latest Alert Details"
FAILING_STEP = "Get Alert Details"
FAILED_RUN = "122386"
ALERT_UUID = "1daad405-3999-4682-ba60-586bec063d25"
ALERT_IRI = f"/api/3/alerts/{ALERT_UUID}"
REFRESHED = "done"
FIELD = "lastRefreshed"

_BAD_TEMPLATE = "/api/3/alerts/{{ vars.steps.Find_Latest_Alert[0]['@id'] }}"

YAML_TEXT = f"""playbooks:
- name: {PLAYBOOK}
  steps:
  - name: Find Latest Alert
    type: find_record
    module: alerts
    query: "{{{{ vars.input.query }}}}"
  - name: {FAILING_STEP}
    type: connector
    connector: cyops_utilities
    operation: api_call
    params:
      method: GET
      iri: {_BAD_TEMPLATE}
  - name: Mark Refreshed
    type: update_record
    module: alerts
    fields:
      {FIELD}: {REFRESHED}
"""

TASK_PROMPT = (
    f'My "{PLAYBOOK}" playbook failed on its last run (run {FAILED_RUN}). '
    "Find out why, fix it, run it again, and confirm the alert record was really "
    f"refreshed (its {FIELD} field should be '{REFRESHED}'). Reply with the cause "
    "and what you verified.\n\n"
    f"The playbook I have open:\n```yaml\n{YAML_TEXT}```"
)

SYSTEM_PROMPT = (
    "You are the FortiSOAR SOC assistant. Use your tools to do what the analyst asks; "
    "do not guess about the state of the platform. Finish with a short answer."
)
HINT = (
    " Workflow tools (debug_run, repair_playbook, verify_effect) are available: each "
    "returns findings that cite evidence ids."
)

#: the cause, in whatever words: doubled / duplicated / prefix / already an IRI.
_CAUSE = re.compile(
    r"doubl|duplicat|twice|prefix|already (a |the )?(full |complete )?(iri|path|url)"
    r"|/api/3/alerts//api/3/alerts|redundant|repeated|appended", re.IGNORECASE)


def _env() -> jinja2.Environment:
    return jinja2.Environment(undefined=jinja2.StrictUndefined)


@dataclass
class Run:
    pk: str
    status: str
    yaml_snapshot: str
    failing_step: str | None = None
    error: str | None = None


@dataclass
class Box:
    """The simulated appliance: one playbook, its runs, one alert record."""

    yaml_text: str = YAML_TEXT
    record: dict[str, Any] = field(default_factory=lambda: {
        "@id": ALERT_IRI, "uuid": ALERT_UUID, "name": "Suspicious login", FIELD: None})
    runs: list[Run] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    _next_pk: int = 122390

    def __post_init__(self) -> None:
        # The run the analyst is asking about, failed on the original YAML.
        self.runs.append(self._execute(FAILED_RUN, YAML_TEXT, apply_effect=False))

    # -- world rules --------------------------------------------------------
    def _steps(self, text: str) -> list[dict[str, Any]]:
        return yaml.safe_load(text)["playbooks"][0]["steps"]

    def _render(self, template: str) -> str:
        ctx = {"vars": {"steps": {"Find_Latest_Alert": [{"@id": ALERT_IRI}]},
                        "input": {"query": "latest"}}}
        return str(_env().from_string(template).render(**ctx))

    def _execute(self, pk: str, text: str, *, apply_effect: bool = True) -> Run:
        try:
            steps = self._steps(text)
            by = {s["name"]: s for s in steps}
            iri = self._render(str(by[FAILING_STEP]["params"]["iri"]))
        except Exception as exc:  # noqa: BLE001 - a broken edit is a failed run
            return Run(pk, "failed", text, FAILING_STEP, f"template error: {exc}")
        if iri.rstrip("/") != ALERT_IRI:
            return Run(pk, "failed", text, FAILING_STEP,
                       f"CS-INTEGRATION-5: 404 Client Error: Not Found for url: "
                       f"https://soar.example.com{iri}")
        if apply_effect:
            self.record[FIELD] = REFRESHED
        return Run(pk, "finished", text)

    def _diag(self, run: Run) -> dict[str, Any]:
        diags = []
        for s in self._steps(run.yaml_snapshot):
            for path, tmpl in _templates(s):
                try:
                    rendered, code = self._render(tmpl), "ok"
                except Exception:  # noqa: BLE001
                    rendered, code = None, "render_error"
                diags.append({"step": s["name"], "arg_path": path, "ok": code == "ok",
                              "code": code, "template": tmpl, "rendered": rendered})
        return {"ok": False, "pb_execution": run.pk, "run_status": run.status,
                "playbook_name": PLAYBOOK, "failing_step": run.failing_step,
                "error_message": run.error, "step_diagnostics": diags, "hints": []}

    # -- the production tool names, simulated -------------------------------
    def why_did_playbook_fail(self, playbook_or_id: str = "", yaml_text: str | None = None):
        failed = [r for r in self.runs if r.status == "failed"]
        run = next((r for r in self.runs if r.pk == str(playbook_or_id)), None)
        if run is None:
            if PLAYBOOK.lower() not in str(playbook_or_id).lower() or not failed:
                return {"ok": False, "code": "no_failed_runs",
                        "message": f"no recent failed run found for {playbook_or_id!r}"}
            run = failed[-1]
        return self._diag(run)

    def list_playbook_runs(self, playbook: str | None = None, include_finished: bool = False,
                           **_: Any):
        rows = [{"pk": r.pk, "status": r.status, "playbook": PLAYBOOK}
                for r in self.runs if include_finished or r.status == "failed"]
        return {"ok": True, "runs": rows[-20:]}

    def get_record(self, iri: str = "", module: str = "", uuid: str = "", **_: Any):
        if ALERT_UUID in f"{iri}{uuid}":
            return {"ok": True, "record": dict(self.record)}
        return {"ok": False, "code": "not_found", "message": "no such record"}

    def run_playbook(self, playbook: str = "", **_: Any):
        if PLAYBOOK.lower() not in str(playbook).lower():
            return {"ok": False, "code": "not_found", "message": f"no playbook {playbook!r}"}
        self._next_pk += 1
        run = self._execute(str(self._next_pk), self.yaml_text)
        self.runs.append(run)
        out: dict[str, Any] = {"ok": run.status == "finished", "status": run.status,
                               "pb_execution": run.pk}
        if run.status != "finished":
            out.update(failing_step=run.failing_step, error_message=run.error)
        return out

    def edit_playbook(self, operations: list | None = None, **_: Any):
        doc = yaml.safe_load(self.yaml_text)
        steps = {s["name"]: s for s in doc["playbooks"][0]["steps"]}
        for op in operations or []:
            if not isinstance(op, dict) or op.get("op") != "update_step":
                return {"ok": False, "code": "unsupported_op",
                        "message": "this offline box applies update_step only"}
            step = steps.get(str(op.get("name") or op.get("step")))
            if step is None:
                return {"ok": False, "code": "step_not_found", "message": op.get("name")}
            for key, val in (op.get("set") or {}).items():
                node = step
                *parents, leaf = key.split(".")
                for p in parents:
                    node = node.setdefault(p, {})
                node[leaf] = val
        self.yaml_text = yaml.safe_dump(doc, sort_keys=False)
        return {"ok": True, "applied": len(operations or []), "verified_id": "v" + "0" * 15,
                "note": "applied to the open playbook"}

    def validate_yaml(self, yaml_text: str = "", **_: Any):
        try:
            yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "errors": []}

    def read_record_for_verify(self, module: str, uuid: str) -> tuple[dict | None, str]:
        if uuid == ALERT_UUID:
            return dict(self.record), ""
        return None, "HTTP 404"


def _templates(step: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in step.items():
        if k in ("name", "type"):
            continue
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out += _templates(v, path + ".")
        elif isinstance(v, str) and "{{" in v:
            out.append((path, v))
    return out


# ---------------------------------------------------------------------------
# tool surface: the real framework list, optionally with the real workflows
# ---------------------------------------------------------------------------

WORKFLOW_NAMES = ("debug_run", "repair_playbook", "verify_effect", "orient",
                  "provision_module", "investigate_alert", "build_playbook")


def tool_list(arm: str) -> list[dict[str, Any]]:
    """OpenAI-shaped tools exactly as the in-product model would see them."""
    from fsr_playbooks.llm import tools as fw

    from fsr_agentic_toolkit.workflow_tools import register_into_framework

    register_into_framework(offer_to_model=arm in ("offered", "hinted"))
    return fw.openai_tools()


class Dispatcher:
    """Routes a tool call into the box; records every call for grading."""

    def __init__(self, box: Box) -> None:
        self.box = box
        self._patch_workflows()

    def _patch_workflows(self) -> None:
        from fsr_agentic_toolkit.workflows import MemoryResultStore, set_result_store
        from fsr_agentic_toolkit.workflows import debug as D
        from fsr_agentic_toolkit.workflows import verify as V

        set_result_store(MemoryResultStore())
        D._diagnose = lambda p, y: self.box.why_did_playbook_fail(p, y)
        V._read_record = self.box.read_record_for_verify

    def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        from fsr_agentic_toolkit.workflow_tools import WORKFLOW_SPECS

        wf = {s.name: s for s in WORKFLOW_SPECS}
        try:
            if name in wf:
                out = wf[name].handler(None, **args)
            elif hasattr(self.box, name) and name != "read_record_for_verify":
                out = getattr(self.box, name)(**args)
            else:
                out = {"ok": False, "code": "unavailable",
                       "message": f"{name} is not available in this environment"}
        except TypeError as exc:
            out = {"ok": False, "code": "bad_arguments", "message": str(exc)}
        self.box.log.append({"tool": name, "args": args, "result": out,
                             "n_runs": len(self.box.runs)})
        return out if isinstance(out, dict) else {"ok": True, "result": out}


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

@dataclass
class Grade:
    fix_pushed: bool
    effect: bool
    read_back: bool
    cites: bool
    workflow_calls: list[str]
    cited_evidence_id: bool
    tool_calls: int

    @property
    def passed(self) -> bool:
        return self.fix_pushed and self.effect and self.read_back and self.cites


def grade(box: Box, final_text: str) -> Grade:
    final = final_text or ""
    last_run_idx = max((i for i, c in enumerate(box.log) if c["tool"] == "run_playbook"),
                       default=-1)
    after = box.log[last_run_idx + 1:] if last_run_idx >= 0 else []
    read_back = any(
        (c["tool"] == "get_record" and ALERT_UUID in json.dumps(c["args"]))
        or (c["tool"] == "verify_effect" and ALERT_UUID in json.dumps(c["args"]))
        for c in after)
    try:
        stored = box._render(str({s["name"]: s for s in box._steps(box.yaml_text)}
                                  [FAILING_STEP]["params"]["iri"]))
        fix_pushed = stored.rstrip("/") == ALERT_IRI
    except Exception:  # noqa: BLE001
        fix_pushed = False
    effect = (box.record.get(FIELD) == REFRESHED and bool(box.runs)
              and box.runs[-1].status == "finished")
    cites = (FAILED_RUN in final and FAILING_STEP.lower() in final.lower()
             and bool(_CAUSE.search(final)))
    wf = [c["tool"] for c in box.log if c["tool"] in WORKFLOW_NAMES]
    ev_ids = {e for c in box.log if c["tool"] in WORKFLOW_NAMES
              for e in (c["result"].get("evidence") or {})}
    cited_ev = any(re.search(rf"\b{re.escape(e)}\b", final) for e in ev_ids)
    return Grade(fix_pushed, effect, read_back, cites, wf, cited_ev, len(box.log))


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

#: a model is (messages, tools) -> {"content": str|None, "tool_calls": [{id,name,arguments}]}
Model = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


def drive(model: Model, arm: str, *, max_rounds: int = 16) -> tuple[Grade, list[dict], Box]:
    box = Box()
    dispatch = Dispatcher(box)
    tools = tool_list(arm)
    system = SYSTEM_PROMPT + (HINT if arm == "hinted" else "")
    messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                      {"role": "user", "content": TASK_PROMPT}]
    final = ""
    for _ in range(max_rounds):
        reply = model(messages, tools)
        calls = reply.get("tool_calls") or []
        messages.append({"role": "assistant", "content": reply.get("content"),
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"],
                                                      "arguments": json.dumps(c["arguments"])}}
                                        for c in calls] or None})
        if not calls:
            final = reply.get("content") or ""
            break
        for c in calls:
            out = dispatch(c["name"], c["arguments"])
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(out, default=str)[:12000]})
    return grade(box, final), messages, box


def frank_model() -> Model:
    """Frank, the free OpenAI-compatible gateway. Never a paid model."""
    from openai import OpenAI

    key, base, name = (os.environ.get(k) for k in
                       ("FRANK_API_KEY", "FRANK_BASE_URL", "FRANK_MODEL"))
    if not (key and base and name):
        raise RuntimeError("FRANK_API_KEY / FRANK_BASE_URL / FRANK_MODEL not set (see .env)")
    client = OpenAI(base_url=base, api_key=key, default_headers={"x-bf-vk": key}, timeout=180)

    def call(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        msgs = [{k: v for k, v in m.items() if v is not None} for m in messages]
        r = client.chat.completions.create(model=name, messages=msgs, tools=tools,
                                           max_tokens=6000, temperature=0)
        m = r.choices[0].message
        out = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out.append({"id": tc.id or _uuid.uuid4().hex[:8], "name": tc.function.name,
                        "arguments": args if isinstance(args, dict) else {}})
        return {"content": m.content, "tool_calls": out}

    return call


def main(argv: list[str] | None = None) -> int:
    import argparse
    import pathlib

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default="hidden,offered,hinted")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    model = frank_model()
    rows = []
    for arm in a.arms.split(","):
        for i in range(a.runs):
            g, msgs, _box = drive(model, arm)
            rows.append({"arm": arm, "run": i, "passed": g.passed, **g.__dict__})
            print(f"{arm:8s} run{i}: {'PASS' if g.passed else 'FAIL'} "
                  f"fix={g.fix_pushed} effect={g.effect} read_back={g.read_back} "
                  f"cites={g.cites} workflows={g.workflow_calls} calls={g.tool_calls}",
                  flush=True)
            if a.out:
                p = pathlib.Path(a.out)
                p.mkdir(parents=True, exist_ok=True)
                (p / f"{arm}_{i}.json").write_text(json.dumps(
                    {"grade": rows[-1], "messages": msgs}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

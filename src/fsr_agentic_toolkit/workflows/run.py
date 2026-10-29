"""run_playbook_yaml: gate -> publish -> trigger -> wait -> read the result, in one call.

The loop an agent otherwise assembles from five tools (compile_playbook_yaml,
publish_playbook, a trigger, wait_for_playbook_run, get_playbook_run) and gets
wrong in the gaps. Here it is one call over the playbook's YAML. A failed gate stops
before anything is deployed; a failed run is handed to debug_run with the YAML,
so the diagnosis rides along with the failure.

``cleanup=true`` deletes what this call created, and only that: a collection or
playbook that already existed on the box is never removed.
"""
from __future__ import annotations

from typing import Any

from fsr_playbooks.mcp_server import _shared

from .debug import debug_run
from .result import WorkflowResult, WorkflowRun


def _gate(run: WorkflowRun, yaml_text: str) -> tuple[dict[str, Any], bool]:
    from ..verbs.tools import _h_compile_playbook_yaml

    out = _h_compile_playbook_yaml(None, yaml_text=yaml_text)
    errors = [e for e in (out.get("errors") or []) if (e.get("severity") or "error") != "warning"]
    warnings = [e for e in (out.get("errors") or []) if e.get("severity") == "warning"]
    fixes = out.get("required_fixes") or []
    ev = run.evidence("playbook", "compile_playbook_yaml(playbook YAML)",
                      f"ok={out.get('ok')}; errors={len(errors)}; warnings={len(warnings)}; "
                      f"required_fixes={len(fixes)}")
    if out.get("ok"):
        run.finding("the playbook passes the compile and reference check", [ev], "high")
        for w in warnings[:5]:
            run.unknown(f"gate warning: {w.get('message')}")
        return out, True
    if any("UNKNOWN_CONNECTOR" in str(e.get("code")) for e in errors if isinstance(e, dict)):
        run.next("list_connectors()", "the connector names installed on this box; use one verbatim", 1)
    for e in (errors or fixes)[:10]:
        msg = e.get("message") if isinstance(e, dict) else str(e)
        where = f" (at {e['path']})" if isinstance(e, dict) and e.get("path") else ""
        hint = e.get("suggestion") or (f"did you mean {e['near']!r}?" if e.get("near") else "") \
            if isinstance(e, dict) else ""
        if hint and hint not in msg:
            msg = f"{msg}{where} -- {hint}"
        else:
            msg = f"{msg}{where}"
        run.finding(f"must fix before publishing: {msg}", [ev], "high")
    return out, False


def _uuid_of(yaml_text: str, name: str) -> str | None:
    """The playbook's uuid straight from the compiled YAML. Naming it lets the run be
    triggered without pyfsr listing every collection to resolve a name (a call that
    exceeds the client timeout on a box with many playbooks)."""
    from ..verbs.tools import _compile_playbook_yaml, _compiled_collection

    for w in _compiled_collection(_compile_playbook_yaml(yaml_text)).get("workflows") or []:
        if isinstance(w, dict) and w.get("name") == name:
            return w.get("uuid")
    return None


def _implicit_trigger(yaml_text: str, name: str) -> bool:
    """True when the playbook declares no trigger step, so the compiler adds a manual Start."""
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
    except Exception:  # noqa: BLE001
        return False
    trig = {"start", "start_on_create", "start_on_update", "start_on_delete", "api_endpoint"}
    for pb in doc.get("playbooks") or []:
        if isinstance(pb, dict) and pb.get("name") == name:
            return not any(isinstance(s, dict) and s.get("type") in trig for s in pb.get("steps") or [])
    return False


def _gate_titles(yaml_text: str) -> list[str]:
    """Titles of the manual_input / approval steps in the YAML, to name in a timeout hint."""
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
    except Exception:  # noqa: BLE001
        return []
    return [str(s["title"]) for pb in (doc.get("playbooks") or []) for s in (pb.get("steps") or [])
            if isinstance(s, dict) and s.get("type") in ("manual_input", "approval") and s.get("title")]


def _silent_empties(client: Any, run_pk: str | None, yaml_text: str) -> list[str]:
    """Variables the YAML sets that came out EMPTY in a run that FINISHED: a green run
    that did nothing (a record-scoped trigger fired with no record, a step output read
    by the wrong path). Read straight off the run's own env, so a hit is a fact, not a
    guess. Best effort: a read failure reports nothing rather than a false alarm."""
    if not run_pk:
        return []
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
        env = dict(client.playbooks.run_env(run_pk).env or {})
    except Exception:  # noqa: BLE001
        return []
    out = []
    for pb in doc.get("playbooks") or []:
        for st in pb.get("steps") or []:
            if not isinstance(st, dict) or st.get("type") != "set_variable":
                continue
            for k, tmpl in (st.get("vars") or {}).items():
                if "{{" in str(tmpl) and k in env and env[k] in ("", None, [], {}, "None"):
                    out.append(f"step {st.get('name')!r} variable {k!r} is empty ({tmpl})")
                elif "{{" in str(tmpl) and k not in env:
                    out.append(f"step {st.get('name')!r} variable {k!r} was never set ({tmpl})")
    return out[:5]


def _cleanup(client: Any, run: WorkflowRun, report: dict[str, Any]) -> None:
    """Delete only what this call created. Anything pre-existing is left alone."""
    created = list(report.get("created") or [])
    untouched = list(report.get("changed") or []) + list(report.get("unchanged") or [])
    if untouched:
        run.unknown("cleanup skipped: the collection already held playbooks this call did "
                    "not create, so nothing was deleted")
        return
    for uuid in created:
        client.playbooks.delete(uuid)
    coll = report.get("collection_uuid")
    kept = False
    if coll:
        # Only remove the collection when nothing else lives in it: a collection that
        # already existed may hold playbooks this call never saw. Unverifiable = keep it.
        try:
            left = (client.workflow_collections.get(coll) or {}).get("workflows")
            kept = left is None or len(left) > 0
        except Exception:  # noqa: BLE001
            kept = True
        if not kept:
            client.workflow_collections.delete(coll)
    if kept:
        run.unknown("the collection was kept because it still holds other playbooks (or could "
                    "not be checked); only the playbook(s) this call created were deleted")
    ev = run.evidence("playbook", report.get("collection") or "collection",
                      f"deleted {len(created)} playbook(s)" + ("" if kept else " and the collection"))
    run.finding("cleanup removed what this call created", [ev], "high")
    run.data["cleaned_up"] = True


def run_playbook_yaml(yaml_text: str, *, playbook: str = "", inputs: dict | None = None,
                 env: dict | None = None, record_uuid: str = "", module: str = "",
                 answers: dict | None = None, timeout: float = 120,
                 overwrite_changed: bool = False, cleanup: bool = False) -> WorkflowResult:
    run = WorkflowRun("run_playbook_yaml")

    gate, ok = _gate(run, yaml_text)
    names = list(gate.get("playbooks") or [])
    run.data["collection"] = gate.get("collection")
    run.data["playbooks"] = names
    if not ok:
        run.next("run_playbook_yaml(yaml_text=<fixed>)", "re-run once every blocking issue is fixed", 2)
        run.next("playbook_yaml_reference(topic=<step type>)", "the accepted keys for the step to fix", 1)
        return run.result("blocked")

    target = playbook or (names[0] if len(names) == 1 else "")
    if not target:
        run.unknown(f"the YAML holds {len(names)} playbooks ({', '.join(names)}); "
                    "name the one to run with playbook=")
        run.next("run_playbook_yaml(yaml_text=..., playbook=<name>)", "pick the playbook to trigger", 2)
        return run.result("blocked")
    if target not in names:
        run.unknown(f"playbook {target!r} is not in the YAML (found: {', '.join(names)})")
        return run.result("blocked")

    if _implicit_trigger(yaml_text, target):
        run.unknown(f"{target!r} declares no trigger step, so a manual 'Start' step was added "
                    "automatically. Add `type: start` (manual), `start_on_create` or `api_endpoint` "
                    "yourself if it should fire any other way")
    client = _shared._live_client()
    from ..verbs.tools import _h_publish_playbook

    try:
        pub = _h_publish_playbook(client, yaml_text=yaml_text, overwrite_changed=overwrite_changed)
    except Exception as exc:  # noqa: BLE001 - surfaced as a blocked result, never raised
        pev = run.evidence("playbook", gate.get("collection") or "collection",
                           f"{type(exc).__name__}: {exc}", ok=False)
        run.unknown(f"publish failed ({pev}): {exc}")
        run.next("run_playbook_yaml(yaml_text=..., overwrite_changed=true)",
                 "only if the error is UI-edited live steps AND the user agrees they are discarded", 3)
        return run.result("blocked")
    report = pub.get("result") or {}
    pev = run.evidence("playbook", pub.get("collection") or "collection",
                       f"new={report.get('new')}; changed={report.get('changed')}; "
                       f"unchanged={report.get('unchanged')}")
    run.finding(f"published {target!r} into collection {pub.get('collection')!r}", [pev], "high")

    status: str = "answered"
    try:
        uuid = _uuid_of(yaml_text, target)
        res = client.playbooks.run_and_wait(
            None if uuid else target, playbook_uuid=uuid, inputs=inputs, env=env,
            record_uuid=record_uuid or None, module=module or None,
            answers=answers, timeout=timeout)
    except TimeoutError:
        titles = _gate_titles(yaml_text)
        run.unknown(f"no terminal state within {timeout:g}s; the run is still going"
                    + (f", most likely waiting on the prompt(s) {titles}" if titles else ""))
        run.next(f"wait_for_playbook_run(playbook='{target}')", "keep waiting for the run", 1)
        if titles:
            run.next(f"run_playbook_yaml(..., answers={{{titles[0]!r}: <button label or value>}})",
                     "re-run answering the manual_input/approval gate", 2)
        run.data["timed_out"] = True
        status = "partial"
        res = None
    except Exception as exc:  # noqa: BLE001
        rev = run.evidence("run", target, f"{type(exc).__name__}: {exc}", ok=False)
        run.unknown(f"trigger failed ({rev}): {exc}")
        run.next("survey_box(goal=...)", "check the playbook's trigger type: a record-scoped "
                 "trigger needs record_uuid + module", 1)
        status = "partial"
        res = None

    if res is not None:
        rev = run.evidence("run", res.pk or res.task_id or target,
                           f"status={res.status}; steps={len(res.steps)}")
        step_ev = {}
        for s in res.steps:
            step_ev[s.name] = run.evidence(
                "step", f"{target}/{s.name}",
                f"{s.status}; {s.duration_ms}ms; {(s.result_preview or '')[:200]}")
        run.data["run"] = {"pk": res.pk, "task_id": res.task_id, "status": res.status,
                           "steps": [{"name": s.name, "status": s.status,
                                      "duration_ms": s.duration_ms,
                                      "result": s.result_preview} for s in res.steps]}
        if res.succeeded:
            run.finding(f"{target!r} ran to completion ({len(res.steps)} step(s))", [rev], "high")
            empties = _silent_empties(client, res.pk, yaml_text)
            for e in empties:
                run.unknown(f"finished, but {e}: the run is green and may have done nothing")
            if empties:
                run.next("playbook_yaml_reference(topic='Reading a previous step\'s output')",
                         "check the path each empty reference reads; a record-scoped trigger "
                         "needs record_uuid + module on this call", 1)
                status = "partial"
        else:
            f = res.failure
            claim = (f"{target!r} ended {res.status}"
                     + (f" at step {f.failing_step!r}: {f.error_message}" if f and f.failing_step else ""))
            run.finding(claim, [step_ev.get(f.failing_step, rev) if f else rev], "high")
            diag = debug_run(res.pk or target, yaml_text=yaml_text)
            run.data["debug_id"] = diag.id
            run.data["diagnosis"] = [x.claim for x in diag.findings[:5]]
            run.next(f"repair_playbook(follow_up='{diag.id}', yaml_text=...)",
                     "apply the mechanical fixes from the diagnosis", 1)
            status = "partial"

    if cleanup and (res is None or run.data.get("timed_out")):
        run.unknown("cleanup skipped: the run did not reach a terminal state, and deleting the "
                    "playbook would orphan it; re-run with cleanup=true once it finishes")
    elif cleanup:
        try:
            _cleanup(client, run, report)
        except Exception as exc:  # noqa: BLE001
            run.unknown(f"cleanup failed: {exc}")
    elif status == "answered":
        run.next("run_playbook_yaml(yaml_text=..., cleanup=true)",
                 "remove the throwaway playbook when it was only a probe", 2)
    return run.result(status)

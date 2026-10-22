"""WorkflowResult: findings must cite evidence the workflow produced; follow-ups reload."""
from __future__ import annotations

import pytest

from fsr_agentic_toolkit.workflows import (
    InvalidCitation,
    MemoryResultStore,
    SqliteResultStore,
    WorkflowRun,
    load_follow_up,
    render_text,
    result_store,
    set_result_store,
)


@pytest.fixture(autouse=True)
def _fresh_store():
    set_result_store(MemoryResultStore())
    yield
    set_result_store(MemoryResultStore())


def test_a_finding_citing_produced_evidence_is_kept():
    run = WorkflowRun("debug_run")
    e = run.evidence("step", "Enrich IP", "status=failed")
    run.finding("Enrich IP failed", [e], "high")
    out = run.result()
    assert out.status == "answered"
    assert out.findings[0].evidence == [e]
    assert out.evidence[e].ref == "Enrich IP"


def test_a_finding_citing_an_unknown_id_is_refused():
    run = WorkflowRun("debug_run")
    run.evidence("step", "A")
    with pytest.raises(InvalidCitation) as exc:
        run.finding("made up", ["e99"])
    assert exc.value.err["code"] == "invalid_evidence_ids"


def test_a_finding_citing_a_failed_lookup_is_refused():
    run = WorkflowRun("debug_run")
    bad = run.evidence("record", "/api/3/alerts/x", ok=False)
    with pytest.raises(InvalidCitation):
        run.finding("record says", [bad])
    assert bad not in run.result().evidence


def test_a_finding_with_no_evidence_is_refused():
    with pytest.raises(InvalidCitation):
        WorkflowRun("orient").finding("trust me", [])


def test_status_is_derived_from_findings_and_unknowns():
    assert WorkflowRun("x").result().status == "blocked"
    run = WorkflowRun("x")
    run.finding("a", [run.evidence("query", "q")])
    run.unknown("could not read b")
    assert run.result().status == "partial"


def test_follow_up_reloads_evidence_and_can_cite_it():
    first = WorkflowRun("debug_run")
    e = first.evidence("step", "Enrich IP", "bad jinja")
    first.finding("Enrich IP failed", [e])
    r1 = first.result()

    found, err = load_follow_up(r1.id, workflow="debug_run")
    assert err is None and found is not None
    second = WorkflowRun("repair_playbook", follow_up=found)
    second.finding("fix the jinja in Enrich IP", [e])  # cites the earlier evidence
    r2 = second.result()
    assert r2.follow_up_of == r1.id and e in r2.evidence


def test_follow_up_refuses_unknown_and_wrong_kind():
    assert load_follow_up("nope")[1]["code"] == "unknown_follow_up"
    r = WorkflowRun("orient").result()
    assert load_follow_up(r.id, workflow="debug_run")[1]["code"] == "wrong_follow_up"
    assert load_follow_up(None) == (None, None)


def test_sqlite_store_round_trips(tmp_path):
    set_result_store(SqliteResultStore(tmp_path / "wf.db"))
    run = WorkflowRun("verify_effect")
    run.finding("field set", [run.evidence("record", "/api/3/alerts/u", "severity=High")])
    r = run.result()
    again = SqliteResultStore(tmp_path / "wf.db").get(r.id)
    assert again == r and result_store().get(r.id) == r


def test_text_render_shows_the_same_fields_as_the_tool_envelope():
    run = WorkflowRun("debug_run")
    e = run.evidence("step", "Enrich IP", "status=failed")
    run.finding("Enrich IP failed", [e])
    run.next("repair_playbook(follow_up=...)", "fix the step", 3)
    r = run.result()
    text = render_text(r)
    for part in ("debug_run [answered]", "Enrich IP failed", "[e1]", "repair_playbook", "tier 3"):
        assert part in text
    tool = r.to_tool()
    assert tool["ok"] is True and tool["findings"][0]["claim"] == "Enrich IP failed"

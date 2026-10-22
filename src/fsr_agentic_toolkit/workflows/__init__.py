"""Agent workflows: multi-step SOAR tasks that return one evidence-backed result.

Every workflow returns a :class:`~fsr_agentic_toolkit.workflows.result.WorkflowResult`
(findings citing evidence, unknowns, suggested next calls, a follow-up id). The
MCP tools and the CLI call the same functions and render the same object.
"""
from .result import (
    Evidence,
    Finding,
    InvalidCitation,
    MemoryResultStore,
    NextStep,
    SqliteResultStore,
    WorkflowResult,
    WorkflowRun,
    load_follow_up,
    render_json,
    render_text,
    result_store,
    set_result_store,
)

__all__ = [
    "Evidence", "Finding", "InvalidCitation", "MemoryResultStore", "NextStep",
    "SqliteResultStore", "WorkflowResult", "WorkflowRun", "load_follow_up",
    "render_json", "render_text", "result_store", "set_result_store",
]

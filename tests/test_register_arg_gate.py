"""Registered workflows must reject unknown arguments.

The framework's arg gate (fsr_playbooks.llm.arg_gate) SKIPS unknown-key
rejection for any `**kwargs` function -- a VAR_KEYWORD signature reads as
"accepts anything". register_into_framework wraps each handler, so without an
explicit signature every workflow would let the model send junk keys straight
through, and the connector's registry contract sweep (test_tool_sweep,
invariant V) rightly fails such a tool. The fix advertises each workflow's
input_schema keys as an explicit KEYWORD_ONLY signature; this test pins that the
gate then refuses an unknown key for every registered workflow.
"""
from __future__ import annotations

import inspect

import pytest

from fsr_agentic_toolkit.workflow_tools import WORKFLOW_SPECS, register_into_framework

arg_gate = pytest.importorskip("fsr_playbooks.llm.arg_gate")


@pytest.fixture(scope="module")
def registered():
    register_into_framework(offer_to_model=False)
    from fsr_playbooks.llm import tools as fw
    return fw.REGISTRY


@pytest.mark.parametrize("name", [s.name for s in WORKFLOW_SPECS])
def test_registered_workflow_rejects_an_unknown_key(registered, name):
    spec = registered[name]
    refusal = arg_gate.check(name, spec.input_schema, spec.fn,
                             {"sweep_not_an_argument": "x"})
    assert refusal is not None, (
        f"{name}: the arg gate accepted an unknown key -- its registered fn still "
        "reads as **kwargs ('accepts anything'). register_into_framework must give "
        "it an explicit signature from input_schema.")
    assert refusal.get("code") == arg_gate.CODE


@pytest.mark.parametrize("name", [s.name for s in WORKFLOW_SPECS])
def test_registered_workflow_signature_has_no_var_keyword(registered, name):
    sig = inspect.signature(registered[name].fn)
    var_kw = [p for p in sig.parameters.values()
              if p.kind is inspect.Parameter.VAR_KEYWORD]
    assert not var_kw, (
        f"{name}: registered fn still exposes **{var_kw[0].name} to the arg gate; "
        "set fn.__signature__ from input_schema so unknown keys are checked.")

"""Keep scenario drives from leaking monkeypatched workflow globals.

tests/workflow_scenario.py's Dispatcher swaps ``verify._read_record`` and
``debug._diagnose`` by DIRECT module assignment (not via pytest's monkeypatch),
so without a restore they persist past the scenario test: a later verify_effect
test then reads the scenario's leftover fake box and comes back 'blocked'
instead of 'answered'/'partial'. The failure only shows in a full-suite run
(order-dependent), which is exactly the kind of leak an autouse restore is for.

Snapshot the workflow module globals before each test and put them back after,
so test order can never change a result.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_workflow_globals():
    from fsr_agentic_toolkit.workflows import debug as D
    from fsr_agentic_toolkit.workflows import verify as V

    saved_diagnose = D._diagnose
    saved_read_record = V._read_record
    try:
        yield
    finally:
        D._diagnose = saved_diagnose
        V._read_record = saved_read_record

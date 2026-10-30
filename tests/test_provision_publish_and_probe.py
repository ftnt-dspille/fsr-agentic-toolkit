"""provision_module publish + verify fixes (offline).

Two bugs made a fully-provisioned module report "partial":

1. The live schema that is_published() reads lags publish()'s own completion
   check on 8.0+, so a single is_published() call right after publish() reads
   False for a module that is in fact committing. _wait_published polls instead.

2. The create-probe used a fixed ``{"name": ...}`` record, which fails HTTP 400
   on a module whose required field is something else (e.g. ``hostname``), so a
   usable module reported "partial". _default_probe builds the probe from the
   module's required fields.
"""

from __future__ import annotations

from fsr_agentic_toolkit.workflows import provision


class _FlipAdmin:
    """is_published() is False for the first ``false_times`` calls, then True."""

    def __init__(self, false_times: int):
        self.false_times = false_times
        self.calls = 0

    def is_published(self, module):
        self.calls += 1
        return self.calls > self.false_times


def test_wait_published_polls_until_live(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    admin = _FlipAdmin(false_times=2)
    # The old code read is_published() once -> would see False on the first call.
    assert admin.is_published("m") is False
    admin.calls = 0  # reset for the real assertion
    assert provision._wait_published(admin, "m", timeout=5, interval=0) is True
    assert admin.calls >= 3, "did not poll past the early False reads"


def test_wait_published_times_out(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    class _NeverLive:
        def is_published(self, module):
            return False

    assert provision._wait_published(_NeverLive(), "m", timeout=0, interval=0) is False


def test_default_probe_fills_required_fields():
    probe = provision._default_probe([
        {"name": "hostname", "type": "text", "required": True},
        {"name": "ip_address", "type": "text"},  # not required -> excluded
        {"name": "criticality", "type": "picklist", "required": True},  # no safe value
        {"name": "count", "type": "integer", "required": True},
    ])
    assert probe["hostname"] == "provision-probe"
    assert probe["count"] == 1
    assert "ip_address" not in probe, "non-required field should not be in the probe"
    assert "criticality" not in probe, "required picklist has no safe guess"
    # The old fixed default would have been {"name": "provision probe"}.
    assert "name" not in probe


def test_default_probe_falls_back_when_no_required_fields():
    assert provision._default_probe([{"name": "note", "type": "text"}]) == {"name": "provision probe"}
    assert provision._default_probe(None) == {"name": "provision probe"}

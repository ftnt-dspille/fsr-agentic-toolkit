"""Unit tests for SchedulesAPI (list/toggle/create/trigger) + the scheduling tools."""


from fsr_agentic_toolkit.verbs.tools import dispatch
from pyfsr.api.schedules import SchedulesAPI

_ENDPOINT = "/api/wf/api/scheduled/"
_TRIGGER_NOW = "/api/wf/api/scheduled/trigger-now/"


# -- recorder (mirrors test_playbooks._Rec: records METHOD/endpoint/data/params) --
class _Rec:
    def __init__(self, *, get_response=None, post_response=None):
        self.calls = []
        self._get = get_response if get_response is not None else {"hydra:member": []}
        self._post = post_response if post_response is not None else {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, None, params))
        return self._get

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return self._post

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, None, params))
        return None


# -- _parse_cron / _utc_offset -------------------------------------------------








# -- create() body construction -------------------------------------------------
def _create(cron="7 2 * * *", **kw):
    rec = _Rec(post_response={"id": "fernet-new", "name": "nightly-recon", "enabled": True})
    out = SchedulesAPI(rec).create("nightly-recon", "/api/3/workflows/abc", cron, typed=False, **kw)
    assert out == {"id": "fernet-new", "name": "nightly-recon", "enabled": True}
    assert len(rec.calls) == 1
    return rec.calls[0]










# -- typed (Pydantic ScheduledTask) return values -------------------------------

_TASK = {
    "id": "fernet-token-1",
    "name": "Purge System Notifications",
    "crontab": {"id": 1, "minute": "0", "hour": "0", "day_of_month": "*", "month_of_year": "*", "day_of_week": "*"},
    "task": "workflow.tasks.purge_system_notifications",
    "enabled": True,
    "total_run_count": 14,
}












# -- trigger_now() -------------------------------------------------------------








# -- delete() -----------------------------------------------------------------




# -- tools (dispatch) ----------------------------------------------------------
class _FakePlaybooks:
    def __init__(self, iri=None):
        self._iri = iri

    def resolve_iri(self, playbook):
        return self._iri


class _FakeSchedules:
    def __init__(self):
        self.create_calls = []
        self.trigger_calls = []
        self.delete_calls = []

    def create(self, name, workflow_iri, cron, **kw):
        self.create_calls.append((name, workflow_iri, cron, kw))
        return {"id": "fernet-new", "name": name, "enabled": kw.get("enabled", True)}

    def trigger_now(self, *, name=None, task_id=None):
        self.trigger_calls.append((name, task_id))
        return {"message": "The associated workflow is successfully triggered"}

    def delete(self, name):
        self.delete_calls.append(name)
        return None


class _FakeClient:
    def __init__(self, iri="/api/3/workflows/pb-uuid"):
        self.playbooks = _FakePlaybooks(iri)
        self.schedules = _FakeSchedules()


def test_tool_schedule_playbook_resolves_name_and_creates():
    c = _FakeClient(iri="/api/3/workflows/pb-uuid")
    out = dispatch(c, "schedule_playbook", {"name": "nightly-recon", "playbook": "Nightly Recon", "cron": "7 2 * * *"})
    assert out == {"id": "fernet-new", "name": "nightly-recon", "enabled": True}
    assert c.schedules.create_calls == [
        (
            "nightly-recon",
            "/api/3/workflows/pb-uuid",
            "7 2 * * *",
            {"timezone": "UTC", "enabled": True, "exit_if_running": True},
        )
    ]


def test_tool_schedule_playbook_with_uuid_skips_resolve():
    c = _FakeClient(iri=None)  # resolve_iri would return None; uuid path must not call it
    out = dispatch(c, "schedule_playbook", {"name": "x", "playbook_uuid": "abc-uuid", "cron": "7 2 * * *"})
    assert out["id"] == "fernet-new"
    assert c.schedules.create_calls[0][1] == "/api/3/workflows/abc-uuid"


def test_tool_schedule_playbook_unknown_playbook_returns_error():
    c = _FakeClient(iri=None)
    out = dispatch(c, "schedule_playbook", {"name": "x", "playbook": "Nope", "cron": "7 2 * * *"})
    assert "error" in out and "Nope" in out["error"]["message"]


def test_tool_schedule_playbook_requires_playbook_or_uuid():
    c = _FakeClient()
    out = dispatch(c, "schedule_playbook", {"name": "x", "cron": "7 2 * * *"})
    assert "error" in out


def test_tool_schedule_playbook_forwards_timezone_and_flags():
    c = _FakeClient()
    dispatch(
        c,
        "schedule_playbook",
        {
            "name": "x",
            "playbook": "P",
            "cron": "0 0 * * 1",
            "timezone": "America/Chicago",
            "enabled": False,
            "exit_if_running": False,
        },
    )
    _, _, _, kw = c.schedules.create_calls[0]
    assert kw == {"timezone": "America/Chicago", "enabled": False, "exit_if_running": False}


def test_tool_trigger_schedule_now_by_name():
    c = _FakeClient()
    out = dispatch(c, "trigger_schedule_now", {"name": "nightly-recon", "confirm": True})
    assert out == {"message": "The associated workflow is successfully triggered"}
    assert c.schedules.trigger_calls == [("nightly-recon", None)]


def test_tool_trigger_schedule_now_by_task_id():
    c = _FakeClient()
    dispatch(c, "trigger_schedule_now", {"task_id": "fernet-direct"})
    assert c.schedules.trigger_calls == [(None, "fernet-direct")]


def test_tool_delete_schedule_calls_delete_by_name():
    c = _FakeClient()
    out = dispatch(c, "delete_schedule", {"name": "nightly-recon", "confirm": True})
    assert out == {"deleted": True, "name": "nightly-recon"}
    assert c.schedules.delete_calls == ["nightly-recon"]


# -- audit ----------------------------------------------------------------------
class _AuditClient:
    """Routes GETs by path: schedule list, workflow lookup, run tables."""

    def __init__(self, *, sched_row, wf=None, live_runs=None, hist_runs=None):
        self._sched = {"hydra:member": [sched_row]}
        self._wf = wf
        self._live = {"hydra:member": live_runs or []}
        self._hist = {"hydra:member": hist_runs or []}
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if endpoint.startswith("/api/wf/api/scheduled"):
            return self._sched
        if endpoint.startswith("/api/3/workflows/"):
            if self._wf is None:
                raise RuntimeError("404")
            return self._wf
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            return self._hist
        if endpoint.startswith("/api/wf/api/workflows/"):
            return self._live
        raise AssertionError(f"unexpected GET {endpoint}")

    @property
    def schedules(self):  # what dispatch handlers reach for on a real client
        return SchedulesAPI(self)


def _sched_row(**over):
    row = {
        "name": "nightly",
        "id": "tok",
        "enabled": True,
        "total_run_count": 5,
        "last_run_at": "2026-08-19T02:07:00Z",
        "crontab": {
            "minute": "7",
            "hour": "2",
            "day_of_month": "*",
            "month_of_year": "*",
            "day_of_week": "*",
            "timezone": "UTC",
        },
        "kwargs": {"wf_iri": "/api/3/workflows/abc"},
    }
    row.update(over)
    return row










def test_schedule_audit_tool_dispatches():
    c = _AuditClient(
        sched_row=_sched_row(),
        wf={"name": "Nightly Recon"},
        live_runs=[{"name": "Nightly Recon", "status": "finished", "created": "t1"}],
    )
    out = dispatch(c, "schedule_audit", {"name": "nightly"})
    assert out["firing"] is True and out["problems"] == []

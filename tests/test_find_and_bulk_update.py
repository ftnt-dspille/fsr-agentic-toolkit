"""find_records (terse query) and bulk_update_records (batch write) handlers."""
from __future__ import annotations

from pyfsr.pagination import HydraPage

from fsr_agentic_toolkit.verbs import tools

_FIELDS = [
    {"name": "name", "type": "text"},
    {"name": "status", "type": "picklists"},
    {"name": "severity", "type": "picklists"},
    {"name": "source", "type": "string"},
]


def _page(members):
    return HydraPage(members=members, total=len(members), page=1, limit=30, raw={})


class _Records:
    def __init__(self, store, members):
        self.store = store
        self._members = members

    def query(self, body):
        self.store["query_body"] = body
        return _page(self._members)

    def update(self, ref, data, *, resolve_picklists=True, strict_picklists=False):
        self.store.setdefault("updates", []).append((ref, data))
        return {"uuid": ref, **data}


class _Client:
    def __init__(self, members=None):
        self.store = {}
        self._members = members if members is not None else []

    def records(self, module):
        return _Records(self.store, self._members)

    def describe_module(self, module):
        return {"module": module, "fields": _FIELDS}


# -- find_records --------------------------------------------------------------

def test_find_records_lowers_predicate_with_itemvalue_and_wildcards():
    c = _Client(members=[{"@id": "/api/3/alerts/u1", "uuid": "u1", "name": "web"}])
    out = tools.dispatch(c, "find_records", {"module": "alerts", "where": 'status=Open and name~"web"'})
    body = c.store["query_body"]
    assert body["logic"] == "AND"
    assert {"field": "status.itemValue", "operator": "eq", "value": "Open"} in body["filters"]
    assert {"field": "name", "operator": "like", "value": "%web%"} in body["filters"]
    assert out["total"] == 1


def test_find_records_empty_where_lists_the_module():
    c = _Client(members=[])
    tools.dispatch(c, "find_records", {"module": "alerts"})
    assert c.store["query_body"]["filters"] == []


def test_find_records_terse_sort_is_lowered():
    c = _Client(members=[])
    tools.dispatch(c, "find_records", {"module": "alerts", "sort": "createDate desc"})
    assert c.store["query_body"]["sort"] == [{"field": "createDate", "direction": "DESC"}]


# -- bulk_update_records -------------------------------------------------------

def test_bulk_update_where_set_builds_rows_from_matches():
    members = [{"uuid": "u1", "name": "a"}, {"uuid": "u2", "name": "b"}]
    c = _Client(members=members)
    out = tools.dispatch(c, "bulk_update_records", {
        "module": "alerts",
        "where": "status=Open and source='Agent Lab'",
        "set": {"status": "Investigating"},
    })
    # It queried with the lowered predicate (picklist -> itemValue).
    assert {"field": "status.itemValue", "operator": "eq", "value": "Open"} in c.store["query_body"]["filters"]
    # One partial update per match: update(uuid, set-fields), the match key excluded.
    assert c.store["updates"] == [
        ("u1", {"status": "Investigating"}),
        ("u2", {"status": "Investigating"}),
    ]
    assert out == {"ok": True, "matched": 2, "updated": 2, "failed": 0}


def test_bulk_update_set_cannot_override_the_match_key():
    members = [{"uuid": "u1"}, {"uuid": "u2"}]
    c = _Client(members=members)
    # A set that (mistakenly) carries a uuid must NOT redirect every row to it.
    tools.dispatch(c, "bulk_update_records", {
        "module": "alerts", "where": "status=Open",
        "set": {"uuid": "ATTACKER", "status": "Investigating"},
    })
    # Each update targets the matched uuid, and uuid is never in the update body.
    assert [ref for ref, _ in c.store["updates"]] == ["u1", "u2"]
    assert all("uuid" not in data and data["status"] == "Investigating" for _, data in c.store["updates"])


def test_bulk_update_explicit_records_path():
    c = _Client()
    out = tools.dispatch(c, "bulk_update_records", {
        "module": "alerts",
        "records": [{"uuid": "u1", "severity": "High"}, {"uuid": "u2", "severity": "Low"}],
    })
    assert c.store["updates"] == [("u1", {"severity": "High"}), ("u2", {"severity": "Low"})]
    assert out["updated"] == 2


def test_bulk_update_refuses_a_row_without_the_match_key():
    c = _Client()
    out = tools.dispatch(c, "bulk_update_records", {
        "module": "alerts",
        "records": [{"severity": "High"}],  # no uuid -> would create, so refused
    })
    assert "no 'uuid'" in out["error"]["message"]
    assert "updates" not in c.store  # never reached the write


def test_bulk_update_no_match_updates_nothing():
    c = _Client(members=[])
    out = tools.dispatch(c, "bulk_update_records", {
        "module": "alerts", "where": "status=Closed", "set": {"status": "Open"},
    })
    assert out == {"ok": True, "matched": 0, "updated": 0, "failed": 0,
                   "note": "no records matched; nothing to update"}
    assert "updates" not in c.store  # never wrote


def test_bulk_update_needs_either_records_or_where_set():
    c = _Client()
    out = tools.dispatch(c, "bulk_update_records", {"module": "alerts"})
    assert "records=[...], or both" in out["error"]["message"]


def test_bulk_update_reports_per_row_failures_without_aborting():
    members = [{"uuid": "u1"}, {"uuid": "u2"}]
    c = _Client(members=members)

    class _FailRecords(_Records):
        def update(self, ref, data, *, resolve_picklists=True, strict_picklists=False):
            if ref == "u1":
                raise RuntimeError("boom")
            self.store.setdefault("updates", []).append((ref, data))
            return {"uuid": ref, **data}

    c.records = lambda module: _FailRecords(c.store, members)  # type: ignore[assignment]
    out = tools.dispatch(c, "bulk_update_records", {
        "module": "alerts", "where": "status=Open", "set": {"status": "Closed"},
    })
    # u1 failed, u2 still updated (the batch does not abort on one failure).
    assert out["ok"] is False and out["failed"] == 1 and out["updated"] == 1
    assert out["failures"][0]["ref"] == "u1" and "boom" in out["failures"][0]["error"]

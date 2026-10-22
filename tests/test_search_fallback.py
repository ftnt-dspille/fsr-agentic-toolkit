"""search_records: an empty index search falls back to a field match and says what ran."""
from __future__ import annotations

from fsr_agentic_toolkit.verbs import tools
from pyfsr.pagination import HydraPage


def _page(members):
    return HydraPage(members=members, total=len(members), page=1, limit=30, raw={})


class _Records:
    def __init__(self, store):
        self.store = store

    def search(self, term="", limit=30):
        self.store.setdefault("searched", []).append(term)
        return _page([])  # the index does not have the record

    def query(self, body):
        self.store["query_body"] = body
        hit = [{"@id": "/api/3/agentlab_assets/u1", "uuid": "u1", "name": "web-01"}]
        return _page(hit if any("web" in f["value"] for f in body["filters"]) else [])


class _Client:
    def __init__(self, fields=None):
        self.store = {}
        self._fields = fields if fields is not None else [
            {"name": "name", "type": "text"}, {"name": "owner", "type": "string"}, {"name": "count", "type": "integer"}]

    def records(self, module):
        return _Records(self.store)

    def describe_module(self, module):
        return {"module": module, "fields": self._fields}


def test_term_search_finds_record_the_index_misses():
    c = _Client()
    out = tools.dispatch(c, "search_records", {"module": "agentlab_assets", "term": "web"})
    assert out["total"] == 1
    assert out["members"][0]["uuid"] == "u1"
    body = c.store["query_body"]
    assert body["logic"] == "OR"
    assert {f["field"] for f in body["filters"]} == {"name", "owner"}
    assert all(f["operator"] == "like" and f["value"] == "%web%" for f in body["filters"])
    assert out["search"]["index_search"]["total"] == 0
    assert out["search"]["field_match"]["total"] == 1


def test_zero_result_reports_the_queries_it_ran():
    c = _Client()
    out = tools.dispatch(c, "search_records", {"module": "agentlab_assets", "term": "nomatch"})
    assert out["total"] == 0
    assert out["search"]["field_match"]["query"]["filters"]


def test_no_text_fields_says_fallback_skipped():
    c = _Client(fields=[])
    out = tools.dispatch(c, "search_records", {"module": "agentlab_assets", "term": "web"})
    assert out["total"] == 0
    assert "skipped" in out["search"]["field_match"]
